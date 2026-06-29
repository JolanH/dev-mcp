"""Command-line entry point and server lifecycle CLI.

The server is global, so there is NO ``--repo`` flag (it learns repos from
``create_task``). Surfaces (Story 3.2):

* ``dev-helper-mcp`` — start the server; scan ``8765→8775`` for a free port.
* ``dev-helper-mcp --port N`` — start, bind EXACTLY ``N`` or fail ``PortUnavailable``
  (strict, no fallback scan).
* ``dev-helper-mcp stop`` / ``dev-helper-mcp --release-lock`` — stop the running
  instance (identity-safe ``SIGTERM`` → clean release) or clear a stale lockfile.

This module is adapter-layer (it imports ``server``/``lock``/``config``); it does
NOT re-implement locking — it *calls* Story 3.1's ``lock.py`` primitives (the
lockfile is read here, the identity guard and PID-liveness are reused) and never
edits them.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import select
import signal
import sys
import time

from . import config, lock, server
from .config import APP_NAME
from .errors import DevHelperError

logger = logging.getLogger(__name__)

#: How long `stop` waits for a signaled instance to release its lockfile before
#: reporting it could not confirm a clean shutdown.
_STOP_WAIT_SECONDS = 10.0
#: Poll cadence while waiting for the lockfile to disappear.
_STOP_POLL_INTERVAL = 0.05
#: Max seconds the ``hook`` command waits for its stdin payload before giving up and
#: falling back to ``os.getcwd()`` — bounds the worst case so an idle/never-closed stdin
#: (a TTY, a misbehaving harness) can never hang the agent's turn.
_HOOK_STDIN_WAIT_SECONDS = 1.0


def _configure_logging() -> None:
    level = os.environ.get("DEV_HELPER_LOG", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Deliberately defines NO ``--repo`` flag — the server is global, so an attempt to
    pass ``--repo`` errors as an unknown argument. ``stop`` is the primary verb;
    ``--release-lock`` is the equivalent flag (Decision A — both route to the same
    stop routine). Factored out so the arg matrix is unit-testable without starting
    a server.
    """
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Global dev-helper MCP server")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["stop", "hook"],
        default=None,
        help="'stop' to signal the running instance to shut down and release the lock; "
        "'hook <blocked|running>' is the Claude Code hook entrypoint (best-effort task "
        "status report — see README).",
    )
    parser.add_argument(
        "state",
        nargs="?",
        choices=["blocked", "running"],
        default=None,
        help="Target status for the 'hook' command: 'blocked' (agent awaiting input) or "
        "'running' (operator answered). Ignored by other commands.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind to this exact port (strict: no fallback scan). Without it, scan 8765-8775.",
    )
    parser.add_argument(
        "--release-lock",
        action="store_true",
        help="Equivalent to the 'stop' subcommand: stop the running instance / clear a stale lock.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse ``argv`` (defaults to ``sys.argv``) into a namespace."""
    return build_parser().parse_args(argv)


# ── stop / --release-lock routine (AC3) ──


def _read_lockfile(path: os.PathLike[str] | str) -> dict | None:
    """Read the lockfile JSON, or ``None`` if missing/unreadable/not an object.

    Mirrors ``lock.py``'s own tolerance: a corrupt or non-object lockfile is treated
    as "no usable record" so the caller clears it as stale.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _terminate(pid: int) -> None:
    """Send a graceful ``SIGTERM`` to ``pid`` — never ``SIGKILL``.

    Isolated as a one-liner so tests can spy on "did stop signal?" without monkey-
    patching the shared ``os.kill`` (which ``lock``'s liveness checks also use).
    """
    os.kill(pid, signal.SIGTERM)


def _clear_stale(path: os.PathLike[str] | str) -> None:
    """Remove a stale lockfile (no live owner). Idempotent; never raises.

    A non-``FileNotFoundError`` ``OSError`` (EACCES/EBUSY/…) must not crash ``stop``
    with a raw traceback — log it and leave the now-known-stale file for the next
    start to reclaim (project-context: never leak a stack trace).
    """
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("could not remove stale lockfile %s: %s", path, exc)


def _await_release(path: os.PathLike[str] | str, pid: int) -> bool:
    """Wait for the signaled instance to release (remove) its lockfile.

    Returns ``True`` once the lockfile is gone (clean shutdown ran 3.1's
    signal/atexit ``release()``). If the process dies without removing it (a crash),
    the now-ownerless file is cleared so the next start is clean.
    """
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not os.path.exists(path):
            return True
        if not lock._pid_alive(pid):
            _clear_stale(path)
            return True
        time.sleep(_STOP_POLL_INTERVAL)
    # Deadline reached. Re-check once: if the process released (or crashed) in the
    # final sleep window, the lockfile is gone — or, if it died without removing it,
    # the file is now ownerless and we clear it rather than falsely report failure.
    if not os.path.exists(path):
        return True
    if not lock._pid_alive(pid):
        _clear_stale(path)
        return True
    return False


def stop_instance() -> int:
    """Stop the running global instance, or clear a stale lock (AC3). Returns an exit code.

    Flow: read the lockfile → if there is no live, identity-matched owner, clear the
    stale lock and report "not running"; otherwise send an identity-safe ``SIGTERM``
    (never ``kill -9``) and wait for the instance to release its lockfile via 3.1's
    signal handler. All benign outcomes (nothing running, stale cleared, clean stop)
    exit 0; only a signaled-but-not-released timeout is non-zero.
    """
    path = config.lockfile_path()
    if not os.path.exists(path):
        print(f"{APP_NAME}: no running instance", flush=True)
        return 0

    record = _read_lockfile(path)
    pid = record.get("pid") if record else None
    # Reject a non-positive / non-int / bool pid before any os.kill (0/negative target
    # a process *group*; bool is an int subclass) — such a lockfile is corrupt ⇒ stale.
    if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0):
        print(f"{APP_NAME}: instance not running; clearing stale lock", flush=True)
        _clear_stale(path)
        return 0

    identity = record.get("identity", "")
    if not isinstance(identity, str):
        identity = ""
    # Identity-safe (3.1's guard): only signal if this is the SAME, still-live instance.
    # A dead PID, a reused PID (Linux identity mismatch), exits here ⇒ clear, never signal.
    if not lock._is_same_live_instance(pid, identity):
        print(f"{APP_NAME}: instance not running; clearing stale lock", flush=True)
        _clear_stale(path)
        return 0

    port = record.get("port")
    try:
        _terminate(pid)
    except ProcessLookupError:
        # Died between the identity check and the signal — same stale path.
        print(f"{APP_NAME}: instance not running; clearing stale lock", flush=True)
        _clear_stale(path)
        return 0
    except PermissionError:
        # The recorded PID is alive but owned by another user — it cannot be our
        # server (we run it as ourselves), so this is a reused-PID stale lock. Clear
        # it without signalling, and never let the raw EPERM escape as a traceback.
        print(f"{APP_NAME}: instance not running; clearing stale lock", flush=True)
        _clear_stale(path)
        return 0

    if _await_release(path, pid):
        print(f"{APP_NAME}: stopped instance (pid={pid}, port={port})", flush=True)
        return 0
    print(
        f"{APP_NAME}: signaled pid={pid} but the lockfile is still present after "
        f"{_STOP_WAIT_SECONDS:.0f}s",
        flush=True,
    )
    return 1


# ── hook entrypoint (Claude Code Notification / UserPromptSubmit) ──

#: The ONLY status transition each hook state is allowed to drive, keyed by target
#: status -> the single ``current`` status it may flip from. ``blocked`` applies only
#: to a ``running`` task; ``running`` (resume) applies only to a ``blocked`` task. This
#: keeps the hook from ever clobbering ``review``/``done`` (a notification or a prompt
#: submitted while a task is awaiting review or closed must not move it).
_HOOK_REQUIRED_CURRENT = {"blocked": "running", "running": "blocked"}


def run_hook(
    state: str | None,
    *,
    stdin_text: str | None = None,
    cwd: str | None = None,
    db_path: str | None = None,
) -> int:
    """Best-effort task-status report for a Claude Code hook. ALWAYS returns 0.

    Resolves the task slug from the agent's worktree (the hook's ``cwd``, read from the
    stdin JSON payload or ``os.getcwd()``) and flips that task between ``running`` and
    ``blocked`` — and ONLY that pair (see :data:`_HOOK_REQUIRED_CURRENT`). Every failure
    mode — no task slug in ``cwd``, an unknown ``state``, an untracked slug, a missing
    DB, an illegal transition, or any unexpected error — is swallowed with at most one
    stderr line and exits 0. The function is engineered to neither raise nor hang: stdin
    reading is time-bounded and even the diagnostic output is suppressed on failure, so a
    hook can never block, crash, or non-zero-exit an agent's turn.

    ``stdin_text`` / ``cwd`` / ``db_path`` are injectable for unit tests; in production
    they default to real stdin, the resolved cwd, and the machine-global DB.
    """
    try:
        if state not in _HOOK_REQUIRED_CURRENT:
            _hook_warn("missing/invalid state (expected blocked|running)")
            return 0

        if cwd is None:
            cwd = _hook_cwd(stdin_text)
        slug = config.slug_from_worktree_cwd(cwd)
        if not slug:
            # Not inside a task worktree (e.g. the main repo) — nothing to report.
            return 0

        import asyncio

        asyncio.run(_apply_hook_status(slug, state, db_path))
    except Exception:  # noqa: BLE001 — a hook must never surface an error to the agent.
        # Even the diagnostics are guarded: a hook's stderr pipe is often closed by the
        # harness, and an unguarded write (BrokenPipeError) would defeat "always exit 0".
        with contextlib.suppress(Exception):
            logger.debug("hook: status report failed; ignoring", exc_info=True)
        _hook_warn("status report skipped (see DEV_HELPER_LOG=DEBUG)")
    return 0


def _hook_warn(message: str) -> None:
    """Emit a single best-effort stderr line for the hook; NEVER raises. A closed stderr
    pipe (common when a harness runs a hook) must not break the always-exit-0 contract."""
    with contextlib.suppress(Exception):
        print(f"{APP_NAME}: hook: {message}", file=sys.stderr)


def _hook_cwd(stdin_text: str | None) -> str:
    """Resolve the hook's working directory: the ``cwd`` field of the stdin JSON payload
    Claude Code sends, falling back to the process cwd when absent/unparseable.

    The result is passed through ``os.path.realpath`` so a symlinked worktree resolves to
    its real ``<repo>.worktrees/<slug>`` location (realpath is lexical for any
    non-existent tail, so a fabricated/absent cwd still normalises cleanly)."""
    if stdin_text is None:
        stdin_text = _read_stdin_bounded()
    cwd = ""
    if stdin_text:
        try:
            payload = json.loads(stdin_text)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("cwd"), str) and payload["cwd"]:
            cwd = payload["cwd"]
    if not cwd:
        cwd = os.getcwd()
    return os.path.realpath(cwd)


def _read_stdin_bounded() -> str:
    """Read the hook's stdin payload without ever blocking indefinitely.

    Claude Code writes the JSON payload then closes stdin (EOF), but a hook may also be
    invoked against an idle TTY or a pipe that never closes — a bare ``sys.stdin.read()``
    would then hang forever and stall the agent's turn. Skip a TTY outright and otherwise
    wait at most ``_HOOK_STDIN_WAIT_SECONDS`` for stdin to become readable."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        ready, _, _ = select.select([sys.stdin], [], [], _HOOK_STDIN_WAIT_SECONDS)
        return sys.stdin.read() if ready else ""
    except Exception:  # noqa: BLE001 — any stdin/select quirk → fall back to os.getcwd().
        return ""


async def _apply_hook_status(slug: str, state: str, db_path: str | None) -> None:
    """Open the store, read ``slug``, and apply the hook transition iff the task is in
    the one ``current`` status that state is allowed to flip from. No-op otherwise.

    If the DB file does not exist yet, return immediately: no server has ever run, so
    there is nothing to report — and a mere notification hook must NOT create the
    machine-global state dir / DB as a side effect."""
    from .core import tasks
    from .store import Store

    effective_db = config.default_db_path() if db_path is None else db_path
    if not os.path.exists(effective_db):
        return

    store = await Store.open(effective_db)
    try:
        task = await store.get_task(slug)
        if task is None or task["status"] != _HOOK_REQUIRED_CURRENT[state]:
            return
        await tasks.update_task(slug, status=state, store=store)
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    _configure_logging()

    if args.command == "hook":
        raise SystemExit(run_hook(args.state))

    if args.command == "stop" or args.release_lock:
        raise SystemExit(stop_instance())

    try:
        server.run(port=args.port)
    except DevHelperError as exc:
        # Lifecycle failure (PortUnavailable on a strict --port; InstanceConflict from
        # the lock). Surface a clear, single-line error and a non-zero exit — never a
        # leaked stack trace (project-context error contract).
        logger.error("%s: %s", exc.code, exc.message)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
