"""Story 3.2 — server lifecycle CLI (``cli.py`` + ``server.py`` strict bind).

OS/process-level tests (``argparse`` / ``os.kill`` / sockets / a real subprocess) —
NO git, so no ``tmp_git_repo``. The autouse ``_isolate_state_dir`` fixture redirects
``XDG_STATE_HOME`` (so the lockfile lives in a per-test tmp dir, never the real
``~/.local/state``) and the autouse ``_guard_project_repo_untouched`` still applies.

Coverage maps to the 4 ACs:
  AC1 — strict ``--port`` binds exactly N or raises ``PortUnavailable`` (NO scan
        fallback); ``port is None`` ⇒ scan. (selection: fast; occupied port: slow.)
  AC2 — bound port recorded in the lockfile + printed in the URL; client poll path
        has no hardcoded port; the parser defines no ``--repo``.
  AC3 — ``stop`` / ``--release-lock`` reads the lockfile, identity-safe ``SIGTERM``,
        clears a stale/dead/mismatched lock without signalling.
  AC4 — a real instance stopped via the routine releases its lockfile on clean exit.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from importlib import resources

import pytest

from dev_helper_mcp import cli, config, lock, server
from dev_helper_mcp.config import BIND_HOST
from dev_helper_mcp.dashboard.render import render_board
from dev_helper_mcp.errors import PortUnavailable


# ── helpers ──


def _dead_pid() -> int:
    """A PID guaranteed dead: spawn a trivial child, reap it, reuse its (freed) PID."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    pid = proc.pid
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    return pid


def _free_port() -> int:
    """An ephemeral port that is free right now (small race window, fine for a test)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((BIND_HOST, 0))
        return sock.getsockname()[1]


def _seed_lock(pid: int, identity: str, port: int = 8765) -> None:
    """Write a lockfile directly (simulating a prior/other instance) into the state dir."""
    config.state_dir().mkdir(parents=True, exist_ok=True)
    config.lockfile_path().write_text(
        json.dumps(
            {"pid": pid, "port": port, "start_ts": "2026-06-26T00:00:00Z", "identity": identity}
        )
    )


def _wait_until(pred, timeout: float = 15.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _port_connectable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect((BIND_HOST, port))
            return True
        except OSError:
            return False


# ── AC1/AC2: argument parsing matrix (fast) ──


def test_parse_default_is_start_no_port():
    ns = cli.parse_args([])
    assert ns.command is None
    assert ns.port is None
    assert ns.release_lock is False


def test_parse_explicit_port():
    assert cli.parse_args(["--port", "9999"]).port == 9999


def test_parse_non_int_port_rejected():
    with pytest.raises(SystemExit):
        cli.parse_args(["--port", "not-a-number"])


def test_parse_stop_subcommand():
    assert cli.parse_args(["stop"]).command == "stop"


def test_parse_release_lock_flag():
    assert cli.parse_args(["--release-lock"]).release_lock is True


def test_parse_rejects_repo_flag():
    """The server is global — there is NO ``--repo`` flag; passing it errors."""
    with pytest.raises(SystemExit):
        cli.parse_args(["--repo", "/some/path"])


def test_parser_defines_no_repo_option():
    option_strings = {
        opt for action in cli.build_parser()._actions for opt in action.option_strings
    }
    assert "--repo" not in option_strings


# ── AC1: strict-vs-scan selection (fast — inject the bind step, no real socket) ──


def test_resolve_bind_none_scans_never_strict(monkeypatch):
    calls: list[str] = []
    sentinel = object()
    monkeypatch.setattr(server, "_bind_scanning", lambda h, r: calls.append("scan") or sentinel)
    monkeypatch.setattr(server, "_bind_strict", lambda h, p: calls.append("strict") or sentinel)
    out = server._resolve_bind(None)
    assert out is sentinel
    assert calls == ["scan"]


def test_resolve_bind_explicit_port_is_strict_never_scans(monkeypatch):
    calls: list[object] = []
    sentinel = object()
    monkeypatch.setattr(server, "_bind_scanning", lambda h, r: calls.append("scan") or sentinel)
    monkeypatch.setattr(
        server, "_bind_strict", lambda h, p: calls.append(("strict", p)) or sentinel
    )
    out = server._resolve_bind(8999)
    assert out is sentinel
    assert calls == [("strict", 8999)]


# ── AC1: strict bind against a real socket (slow) ──


@pytest.mark.slow
def test_strict_port_free_binds_exactly_on_loopback():
    port = _free_port()
    sock = server._bind_strict(BIND_HOST, port)
    try:
        assert sock.getsockname()[1] == port
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


@pytest.mark.slow
def test_strict_port_occupied_raises_without_fallback(monkeypatch):
    """An occupied explicit ``--port`` ⇒ ``PortUnavailable`` and NEVER a scan fallback."""
    holder = lock.bind_socket(BIND_HOST, 0)  # ephemeral, SO_REUSEADDR
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        monkeypatch.setattr(
            server,
            "_bind_scanning",
            lambda *a, **k: pytest.fail("strict --port must NOT fall back to scanning"),
        )
        with pytest.raises(PortUnavailable) as excinfo:
            server._resolve_bind(port)
        assert excinfo.value.code == "PortUnavailable"
        assert excinfo.value.details.get("port") == port
    finally:
        holder.close()


# ── AC2: client poll path has no hardcoded port (Decision B — verify, don't add) ──


def test_client_poller_has_no_hardcoded_port():
    poller = (
        resources.files("dev_helper_mcp.dashboard").joinpath("static/poller.js").read_text("utf-8")
    )
    assert 'fetch("/state"' in poller, "poller must fetch the relative same-origin /state"
    assert "8765" not in poller
    assert "http://127.0.0.1" not in poller
    assert "http://localhost" not in poller


def test_rendered_board_has_no_hardcoded_port():
    html = render_board({"generated_at": "2026-06-26T11:00:00Z", "tasks": [], "warnings": []})
    assert "8765" not in html
    assert "http://127.0.0.1" not in html
    assert "http://localhost" not in html


# ── AC3: stop / --release-lock unit cases (fast) ──


def test_stop_no_lockfile_reports_not_running(capsys):
    assert cli.stop_instance() == 0
    assert "no running instance" in capsys.readouterr().out


def test_stop_corrupt_lockfile_clears_stale(capsys):
    config.state_dir().mkdir(parents=True, exist_ok=True)
    config.lockfile_path().write_text("{not valid json")
    assert cli.stop_instance() == 0
    assert not config.lockfile_path().exists()
    assert "clearing stale lock" in capsys.readouterr().out


def test_stop_dead_pid_clears_without_signalling(monkeypatch):
    signalled: list[int] = []
    monkeypatch.setattr(cli, "_terminate", lambda pid: signalled.append(pid))
    _seed_lock(pid=_dead_pid(), identity="bogus:identity", port=9999)
    assert cli.stop_instance() == 0
    assert signalled == []
    assert not config.lockfile_path().exists()


@pytest.mark.parametrize("bad_pid", [0, -1, True, "1234", None])
def test_stop_non_positive_pid_is_stale_never_signalled(monkeypatch, bad_pid):
    signalled: list[int] = []
    monkeypatch.setattr(cli, "_terminate", lambda pid: signalled.append(pid))
    _seed_lock(pid=bad_pid, identity="x")
    assert cli.stop_instance() == 0
    assert signalled == []
    assert not config.lockfile_path().exists()


@pytest.mark.skipif(sys.platform != "linux", reason="identity guard is Linux-only (/proc)")
def test_stop_identity_mismatch_does_not_signal(monkeypatch):
    """A reused PID (live, but identity-mismatched) must never be SIGTERMed."""
    signalled: list[int] = []
    monkeypatch.setattr(cli, "_terminate", lambda pid: signalled.append(pid))
    real = lock._identity_token(os.getpid())
    assert real is not None
    _seed_lock(pid=os.getpid(), identity=real + "-reused")
    assert cli.stop_instance() == 0
    assert signalled == []
    assert not config.lockfile_path().exists()


def test_stop_live_matched_signals_and_releases(monkeypatch, capsys):
    """A live, identity-matched instance is SIGTERMed and reported stopped."""
    identity = lock._identity_token(os.getpid())
    if identity is None:
        identity = ""  # degraded platform: PID-liveness only, our live pid matches
    signalled: list[int] = []
    monkeypatch.setattr(cli, "_terminate", lambda pid: signalled.append(pid))
    monkeypatch.setattr(cli, "_await_release", lambda path, pid: True)
    _seed_lock(pid=os.getpid(), identity=identity)
    assert cli.stop_instance() == 0
    assert signalled == [os.getpid()]
    assert "stopped instance" in capsys.readouterr().out


def test_stop_process_vanishes_between_check_and_signal(monkeypatch, capsys):
    """``_terminate`` raising ``ProcessLookupError`` (a race) ⇒ treat as stale, clear, ok."""
    identity = lock._identity_token(os.getpid()) or ""

    def _raise_gone(pid):
        raise ProcessLookupError

    monkeypatch.setattr(cli, "_terminate", _raise_gone)
    _seed_lock(pid=os.getpid(), identity=identity)
    assert cli.stop_instance() == 0
    assert not config.lockfile_path().exists()
    assert "clearing stale lock" in capsys.readouterr().out


def test_stop_permission_error_clears_without_traceback(monkeypatch, capsys):
    """A recorded PID alive but owned by another user (``os.kill`` ⇒ EPERM) is a
    reused-PID stale lock — cleared, never signalled, and no raw traceback escapes."""
    identity = lock._identity_token(os.getpid()) or ""

    def _raise_eperm(pid):
        raise PermissionError

    monkeypatch.setattr(cli, "_terminate", _raise_eperm)
    _seed_lock(pid=os.getpid(), identity=identity)
    assert cli.stop_instance() == 0  # no PermissionError propagates
    assert not config.lockfile_path().exists()
    assert "clearing stale lock" in capsys.readouterr().out


# ── _await_release (fast — no real instance) ──


def test_await_release_clears_when_pid_dies_during_wait():
    """A dead PID during the wait ⇒ the ownerless lockfile is cleared and True returned."""
    _seed_lock(pid=_dead_pid(), identity="bogus")
    path = config.lockfile_path()
    assert cli._await_release(path, pid=_dead_pid()) is True
    assert not path.exists()


def test_await_release_times_out_false_for_live_held_lock(monkeypatch):
    """A live process that never releases ⇒ timeout returns False (the exit-1 path)."""
    monkeypatch.setattr(cli, "_STOP_WAIT_SECONDS", 0.15)
    monkeypatch.setattr(cli, "_STOP_POLL_INTERVAL", 0.02)
    _seed_lock(pid=os.getpid(), identity="x")  # our own live PID holds it
    path = config.lockfile_path()
    assert cli._await_release(path, pid=os.getpid()) is False
    assert path.exists()  # not cleared — a live owner's lock is never removed here


# ── main() dispatch (fast) ──


def test_main_dispatches_start(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(server, "run", lambda port=None: seen.__setitem__("port", port))
    cli.main([])
    assert seen == {"port": None}
    cli.main(["--port", "9001"])
    assert seen["port"] == 9001


def test_main_stop_exits_with_routine_code(monkeypatch):
    def fake_stop():
        seen["called"] = True
        return 0

    seen: dict[str, bool] = {}
    monkeypatch.setattr(cli, "stop_instance", fake_stop)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["stop"])
    assert excinfo.value.code == 0
    assert seen["called"]


def test_main_release_lock_runs_stop_routine(monkeypatch):
    def fake_stop():
        seen["called"] = True
        return 0

    seen: dict[str, bool] = {}
    monkeypatch.setattr(cli, "stop_instance", fake_stop)
    with pytest.raises(SystemExit):
        cli.main(["--release-lock"])
    assert seen["called"]


def test_main_strict_port_unavailable_exits_nonzero_no_traceback(monkeypatch, caplog):
    def _boom(port=None):
        raise PortUnavailable("port 9 is already in use", details={"port": 9})

    monkeypatch.setattr(server, "run", _boom)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--port", "9"])
    assert excinfo.value.code == 1
    assert any("PortUnavailable" in rec.message for rec in caplog.records)
    # "no traceback": the error is logged as a single message line, never with exc_info
    # (which would render a stack trace) — honour the project's no-stack-trace-leak rule.
    assert all(rec.exc_info is None for rec in caplog.records)


# ── AC2 + AC3 + AC4: real instance — bound port → lockfile + URL; stop → clean release ──


@pytest.mark.slow
def test_real_instance_records_bound_port_then_stop_releases():
    port = _free_port()
    env = dict(os.environ)  # carries the autouse XDG_STATE_HOME override
    env["DEV_HELPER_LOG"] = "WARNING"
    proc = subprocess.Popen(
        [sys.executable, "-m", "dev_helper_mcp", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lockpath = config.lockfile_path()
    try:
        assert _wait_until(lambda: lockpath.exists()), "instance never wrote the lockfile"
        assert _wait_until(lambda: _port_connectable(port)), "instance never started serving"

        # AC2: the BOUND port is recorded in the lockfile.
        record = json.loads(lockpath.read_text())
        assert record["pid"] == proc.pid
        assert record["port"] == port

        # AC3/AC4: the in-process stop routine reads the lockfile, SIGTERMs the real
        # instance, which releases its lock on clean uvicorn shutdown.
        assert cli.stop_instance() == 0
        assert not lockpath.exists()
        # Drain both pipes and reap together via communicate() — avoids the textbook
        # Popen deadlock of read()-after-wait() should the child ever out-write the
        # pipe buffer before exit. Captures the startup banner for the URL assertion.
        out, _ = proc.communicate(timeout=10)
        rc = proc.returncode
        # Clean shutdown: either uvicorn's graceful return (0) or 3.1's signal handler
        # re-raising the SIGTERM we sent (-SIGTERM, the conventional "terminated by
        # signal" exit). The point is the lock was released above — NEVER -SIGKILL
        # (that is the unclean path, covered by 3.1's stale-lock reclaim, not here).
        assert rc in (0, -signal.SIGTERM), f"unexpected exit code {rc}"
        assert rc != -signal.SIGKILL

        # AC2: the dashboard URL printed on startup reflects the bound port.
        assert f"http://{BIND_HOST}:{port}/" in out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
