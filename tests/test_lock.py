"""Story 3.1 — machine-global single-instance lockfile (``lock.py``).

OS-level tests (``os.open`` / ``os.kill`` / ``/proc`` / sockets) — NO git, so no
``tmp_git_repo``. The autouse ``_isolate_state_dir`` fixture redirects
``XDG_STATE_HOME`` so the real ``~/.local/state`` is never touched, and the autouse
``_guard_project_repo_untouched`` still applies.

Coverage maps to the 5 ACs:
  AC1 — atomic ``O_CREAT|O_EXCL`` create + JSON payload (+ EEXIST path reached).
  AC2 — live PID + identity match ⇒ ``InstanceConflict`` (never raw EADDRINUSE).
  AC3 — dead PID / identity mismatch / corrupt file ⇒ atomic-rename reclaim.
  AC4 — port-bind authoritative: ``EADDRINUSE`` ⇒ ``InstanceConflict`` (slow).
  AC5 — non-Linux / no-``/proc`` degrade ⇒ PID-liveness only + startup warning.
"""

import json
import logging
import os
import re
import subprocess
import sys

import pytest

from dev_helper_mcp import config, lock
from dev_helper_mcp.config import BIND_HOST
from dev_helper_mcp.errors import InstanceConflict

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _seed_lock(pid: int, identity: str, port: int = 8765) -> None:
    """Write a lockfile directly (simulating a prior instance) into the state dir."""
    config.state_dir().mkdir(parents=True, exist_ok=True)
    config.lockfile_path().write_text(
        json.dumps(
            {"pid": pid, "port": port, "start_ts": "2026-06-26T00:00:00Z", "identity": identity}
        )
    )


def _dead_pid() -> int:
    """A PID guaranteed dead: spawn a trivial child, reap it, reuse its (freed) PID."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    pid = proc.pid
    # Confirm it is actually gone before any test relies on ESRCH.
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    return pid


# ── AC1: atomic create + payload ──


def test_acquire_creates_lockfile_with_payload():
    handle = lock.acquire(8765)
    path = config.lockfile_path()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["pid"] == os.getpid()
    assert data["port"] == 8765
    assert _ISO_Z.match(data["start_ts"])
    assert "identity" in data and isinstance(data["identity"], str)
    # 0o600 — owner-only.
    assert (path.stat().st_mode & 0o777) == 0o600
    assert handle.pid == os.getpid()


def test_second_acquire_hits_eexist_and_refuses():
    """A second acquire while the first is held reaches the EEXIST path; since the
    holder is this live, identity-matched process it refuses (AC1 atomicity + AC2)."""
    lock.acquire(8765)
    with pytest.raises(InstanceConflict):
        lock.acquire(8766)


# ── AC3: stale-lock reclaim ──


def test_stale_dead_pid_reclaim():
    _seed_lock(pid=_dead_pid(), identity="bogus:identity", port=9999)
    handle = lock.acquire(8765)
    data = json.loads(config.lockfile_path().read_text())
    assert data["pid"] == os.getpid()
    assert data["port"] == 8765
    assert handle.pid == os.getpid()


def test_corrupt_lockfile_reclaim():
    config.state_dir().mkdir(parents=True, exist_ok=True)
    config.lockfile_path().write_text("{not valid json")
    lock.acquire(8765)
    data = json.loads(config.lockfile_path().read_text())
    assert data["pid"] == os.getpid()


@pytest.mark.parametrize("bad_pid", [0, -1, True, "1234", None])
def test_non_positive_int_pid_is_reclaimed_not_probed(bad_pid):
    """A lockfile pid that is not a positive non-bool int must never reach os.kill
    (0/negative target a process *group* and never raise ESRCH; bool is an int
    subclass that would probe PID 1). Such a lockfile is treated as stale ⇒ reclaim."""
    config.state_dir().mkdir(parents=True, exist_ok=True)
    config.lockfile_path().write_text(
        json.dumps(
            {"pid": bad_pid, "port": 9999, "start_ts": "2026-06-26T00:00:00Z", "identity": "x"}
        )
    )
    handle = lock.acquire(8765)
    assert json.loads(config.lockfile_path().read_text())["pid"] == os.getpid()
    assert handle.pid == os.getpid()


# ── AC2: live PID + identity match ⇒ refuse ──


def test_live_pid_identity_match_refuses():
    """Seed our own live PID + the real current identity ⇒ acquire refuses."""
    identity = lock._identity_token(os.getpid())
    if identity is None:
        pytest.skip("identity guard unavailable on this platform (covered by AC5 degrade tests)")
    _seed_lock(pid=os.getpid(), identity=identity)
    with pytest.raises(InstanceConflict) as excinfo:
        lock.acquire(8766)
    assert excinfo.value.code == "InstanceConflict"
    assert excinfo.value.details.get("pid") == os.getpid()
    # The lockfile is NOT clobbered on refusal.
    assert json.loads(config.lockfile_path().read_text())["identity"] == identity


# ── AC2/AC3: PID reuse ⇒ reclaim (Linux identity guard) ──


@pytest.mark.skipif(sys.platform != "linux", reason="identity guard is Linux-only (/proc)")
def test_pid_reuse_reclaims():
    """Live PID but a mismatched identity ⇒ the guard catches the reuse ⇒ reclaim."""
    real = lock._identity_token(os.getpid())
    assert real is not None
    _seed_lock(pid=os.getpid(), identity=real + "-reused")  # tweak ⇒ mismatch
    handle = lock.acquire(8765)
    data = json.loads(config.lockfile_path().read_text())
    assert data["pid"] == os.getpid()
    assert data["identity"] == real  # rewritten with the real token
    assert handle.identity == real


# ── AC5: non-Linux / no-/proc degrade ──


def test_non_linux_degrade_refuses_live_and_warns(monkeypatch, caplog):
    """Platform forced non-Linux ⇒ PID-liveness only + warning; a live PID refuses."""
    monkeypatch.setattr(lock.sys, "platform", "darwin")
    _seed_lock(pid=os.getpid(), identity="anything")
    with caplog.at_level(logging.WARNING, logger="dev_helper_mcp.lock"):
        with pytest.raises(InstanceConflict):
            lock.acquire(8766)
    assert any("PID-liveness only" in rec.message for rec in caplog.records)


def test_non_linux_degrade_dead_pid_reclaims(monkeypatch):
    monkeypatch.setattr(lock.sys, "platform", "darwin")
    _seed_lock(pid=_dead_pid(), identity="anything")
    lock.acquire(8765)
    assert json.loads(config.lockfile_path().read_text())["pid"] == os.getpid()


def test_no_proc_on_linux_degrades_and_warns(monkeypatch, caplog):
    """Linux but /proc entirely unavailable (no boot_id) ⇒ genuine degrade:
    PID-liveness only + the startup warning."""
    monkeypatch.setattr(lock.sys, "platform", "linux")
    monkeypatch.setattr(lock, "_read_boot_id", lambda: None)
    monkeypatch.setattr(lock, "_read_starttime", lambda pid: None)
    _seed_lock(pid=os.getpid(), identity="anything")
    with caplog.at_level(logging.WARNING, logger="dev_helper_mcp.lock"):
        with pytest.raises(InstanceConflict):
            lock.acquire(8766)
    assert any("PID-liveness only" in rec.message for rec in caplog.records)


def test_proc_stat_unreadable_live_pid_refuses_without_degrade_warning(monkeypatch, caplog):
    """Linux + /proc present (boot_id readable) but a *live* PID's stat read fails ⇒
    refuse (liveness re-confirmed), and NO 'guard unavailable' degrade warning — that
    warning is reserved for a genuine platform degrade (non-Linux / no /proc)."""
    monkeypatch.setattr(lock.sys, "platform", "linux")
    monkeypatch.setattr(lock, "_read_starttime", lambda pid: None)
    _seed_lock(pid=os.getpid(), identity="anything")
    with caplog.at_level(logging.WARNING, logger="dev_helper_mcp.lock"):
        with pytest.raises(InstanceConflict):
            lock.acquire(8766)
    assert not any("PID-liveness only" in rec.message for rec in caplog.records)


def test_proc_stat_vanished_for_dying_pid_reclaims(monkeypatch):
    """Regression: the recorded PID is alive at the liveness check but dies before the
    /proc stat read (identity token None). On Linux with /proc present this must be
    treated as stale ⇒ reclaim, NOT a false InstanceConflict."""
    calls = {"n": 0}

    def fake_alive(pid):
        calls["n"] += 1
        return calls["n"] == 1  # alive on the first check, dead by the re-confirm

    monkeypatch.setattr(lock.sys, "platform", "linux")
    monkeypatch.setattr(lock, "_pid_alive", fake_alive)
    monkeypatch.setattr(lock, "_read_starttime", lambda pid: None)
    _seed_lock(pid=os.getpid(), identity="anything")
    lock.acquire(8765)  # reclaims — the holder died mid-check
    assert json.loads(config.lockfile_path().read_text())["pid"] == os.getpid()


# ── Release: only-if-owned ──


def test_release_removes_owned_lockfile():
    handle = lock.acquire(8765)
    assert config.lockfile_path().exists()
    handle.release()
    assert not config.lockfile_path().exists()
    # Idempotent.
    handle.release()


def test_release_does_not_delete_another_instances_lock():
    handle = lock.acquire(8765)
    # Simulate another instance reclaiming the lock after us.
    config.lockfile_path().write_text(
        json.dumps(
            {"pid": 999999, "port": 8765, "start_ts": "2026-06-26T00:00:00Z", "identity": "other"}
        )
    )
    handle.release()  # must be a no-op — not ours
    assert config.lockfile_path().exists()
    assert json.loads(config.lockfile_path().read_text())["pid"] == 999999


def test_release_when_lockfile_already_gone_is_noop():
    handle = lock.acquire(8765)
    os.remove(config.lockfile_path())
    handle.release()  # no exception


# ── AC4: port-bind is the authoritative mutex (real socket → slow) ──


@pytest.mark.slow
def test_port_bind_is_authoritative_even_after_reclaim():
    holder = lock.bind_socket(BIND_HOST, 0)  # ephemeral port
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        # Reclaim a stale lock first to prove the bind stays authoritative
        # REGARDLESS of lock state (AC4).
        _seed_lock(pid=_dead_pid(), identity="bogus", port=port)
        lock.acquire(port)  # reclaims (dead PID) — succeeds
        with pytest.raises(InstanceConflict) as excinfo:
            lock.bind_socket(BIND_HOST, port)
        assert excinfo.value.details.get("port") == port
    finally:
        holder.close()


@pytest.mark.slow
def test_bind_socket_is_loopback():
    sock = lock.bind_socket(BIND_HOST, 0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()
