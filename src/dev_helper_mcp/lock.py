"""Machine-global single-instance lockfile (AR-10): atomic acquire, stale-lock
recovery, and the authoritative port-bind mutex.

This is the *process-singleton* guard — exactly one ``dev-helper-mcp`` per
machine — distinct from the per-repo mutation mutex (AR-14, ``git/repo_lock``).

Layering: ``lock.py`` is ADAPTER-layer (project-context.md lists it beside
``server.py``/``cli.py``) and is therefore NOT in the SDK-isolation seam. In
practice it stays OS-only — ``os``/``sys``/``json``/``errno``/``socket`` plus our
own ``config``/``util``/``errors`` — for testability and v2-migration cleanliness;
it imports no ``mcp``/``starlette``/``uvicorn``, no ``subprocess``, no DB, no git.

Design (two cooperating guards):
- **Lockfile** (``server.lock`` = ``{pid, port, start_ts, identity}``): the fast,
  diagnostic guard. ``acquire`` creates it atomically with ``O_CREAT|O_EXCL``; on
  ``EEXIST`` it refuses only if the recorded PID is a *live, identity-matched*
  instance, otherwise it reclaims via atomic rename. This catches a second live
  instance even when it would bind a *different* port.
- **Port bind** (``bind_socket``): the *authoritative* mutex. ``EADDRINUSE`` ⇒
  ``InstanceConflict`` regardless of lock state, so a PID-reuse false positive or
  a reclaim race is benign — the bind, not the lockfile, is the real guarantee.

The unclean path is intentional: ``kill -9`` leaves a stale lock, which a later
start reclaims (PID dead, or the Linux ``/proc`` identity guard catches PID reuse).
There is no reconciliation sweep (v1 non-goal).
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import sys

from . import config, util
from .errors import InstanceConflict

logger = logging.getLogger(__name__)

#: Degraded identity marker stored when the Linux ``/proc`` guard is unavailable
#: (non-Linux, or ``/proc/<pid>/stat`` unreadable). The guard then falls back to
#: PID-liveness only; the port-bind mutex carries the single-instance guarantee.
_DEGRADED_IDENTITY = ""


class LockHandle:
    """Ownership token for an acquired lockfile.

    ``release()`` removes the lockfile ONLY if it is still ours (same ``pid`` +
    ``identity``), so a reclaim by another instance is never clobbered. It is
    idempotent — safe to call from a signal handler, ``atexit``, and a ``finally``.
    """

    def __init__(self, path: os.PathLike[str] | str, pid: int, port: int, identity: str) -> None:
        self.path = path
        self.pid = pid
        self.port = port
        self.identity = identity
        self._released = False

    def release(self) -> None:
        """Remove the lockfile if this process still owns it; otherwise a no-op."""
        if self._released:
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                current = json.load(fh)
        except OSError, ValueError:
            # Gone or unreadable — nothing of ours left to remove.
            self._released = True
            return
        if not (
            isinstance(current, dict)
            and current.get("pid") == self.pid
            and current.get("identity") == self.identity
        ):
            # Reclaimed by another instance, or corrupt/non-object JSON — never our
            # lock to delete (an ``isinstance`` guard also avoids ``AttributeError``
            # on a valid-JSON non-dict like ``42``/``[]``).
            self._released = True
            return
        # A non-``FileNotFoundError`` ``os.remove`` failure (e.g. EACCES/EBUSY) leaves
        # ``_released`` False and propagates, so a later atexit/``finally`` call retries
        # rather than silently leaking the lock. Only mark released on a confirmed remove.
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass  # already gone — treat as released
        self._released = True
        logger.info("Released single-instance lock (pid=%s, port=%s)", self.pid, self.port)


# ── OS identity token + liveness (Task 2) ──


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a live process.

    ``os.kill(pid, 0)`` probes without signalling: ESRCH ⇒ dead; EPERM ⇒ alive
    but owned by another user (still alive); no error ⇒ alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # ESRCH
        return False
    except PermissionError:  # EPERM — alive, not ours
        return True
    return True


def _read_boot_id() -> str | None:
    """Linux boot id (ties a starttime to this boot), or ``None`` if unavailable."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_starttime(pid: int) -> str | None:
    """Field 22 (``starttime``, clock ticks since boot) of ``/proc/<pid>/stat``.

    Parsing trap: field 2 (``comm``) is paren-wrapped and may contain spaces and
    parens, so split on the LAST ``)`` and count whitespace tokens from there.
    Field 22 overall is index 19 of that post-``)`` split (fields 1–2 precede it).
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            line = fh.read()
    except OSError:
        return None
    rparen = line.rfind(")")
    if rparen == -1:
        return None
    fields = line[rparen + 1 :].split()
    if len(fields) < 20:
        return None
    return fields[19]


def _identity_token(pid: int) -> str | None:
    """Linux identity token ``f"{boot_id}:{starttime}"`` for ``pid``.

    Returns ``None`` when the guard cannot run (non-Linux, or ``/proc`` missing) —
    callers then degrade to PID-liveness only.
    """
    if sys.platform != "linux":
        return None
    boot_id = _read_boot_id()
    starttime = _read_starttime(pid)
    if boot_id is None or starttime is None:
        return None
    return f"{boot_id}:{starttime}"


def _is_same_live_instance(pid: int, stored_identity: str) -> bool:
    """Whether the lockfile's recorded process is the same, still-running instance.

    ``True`` ⇒ refuse (a live instance holds the lock); ``False`` ⇒ reclaim (dead,
    or the Linux identity guard caught PID reuse). When the guard is genuinely
    unavailable (non-Linux / no ``/proc``) it degrades to PID-liveness only and
    warns — the port-bind mutex remains authoritative (AC5).
    """
    if not _pid_alive(pid):
        return False
    current_identity = _identity_token(pid)
    if current_identity is not None:
        # Match ⇒ same live instance ⇒ refuse. Mismatch ⇒ the PID was reused ⇒ reclaim.
        return current_identity == stored_identity
    # No identity token. Distinguish a genuine platform degrade (non-Linux, or
    # ``/proc`` absent) from a transient per-PID read miss: on Linux with ``/proc``
    # present, a missing ``/proc/<pid>/stat`` means the PID died between the liveness
    # check and the read ⇒ it is stale, so re-confirm liveness and reclaim if gone
    # (rather than falsely refusing as though the guard were unavailable).
    if sys.platform == "linux" and _read_boot_id() is not None:
        return _pid_alive(pid)
    logger.warning(
        "identity guard unavailable on %s; using PID-liveness only — "
        "the port-bind mutex remains authoritative",
        sys.platform,
    )
    return True


# ── Lockfile payload + atomic primitives (Tasks 1, 3) ──


def _payload(pid: int, port: int, identity: str) -> bytes:
    """The lockfile JSON. ``{pid, port, start_ts}`` are the AC1-named keys verbatim;
    ``identity`` is the extra field the Linux guard needs."""
    return json.dumps(
        {"pid": pid, "port": port, "start_ts": util.now_iso(), "identity": identity}
    ).encode("utf-8")


def _reclaim(path: os.PathLike[str] | str, pid: int, port: int, identity: str) -> LockHandle:
    """Take over a stale lock via atomic rename (never an in-place edit).

    Two racing reclaimers each rename their own temp file — last writer wins on the
    lockfile, and the port-bind mutex then rejects the loser, so the race is benign.
    """
    tmp = f"{os.fspath(path)}.tmp.{pid}"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, _payload(pid, port, identity))
    finally:
        os.close(fd)
    os.replace(tmp, path)  # atomic on POSIX
    logger.warning("Reclaimed stale single-instance lock (pid=%s, port=%s)", pid, port)
    return LockHandle(path, pid, port, identity)


def _resolve_existing(
    path: os.PathLike[str] | str, port: int, pid: int, identity: str
) -> LockHandle:
    """EEXIST path: refuse if a live identity-matched instance holds it; else reclaim."""
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
    except OSError, ValueError:
        # Unparseable/corrupt ⇒ treat as stale.
        return _reclaim(path, pid, port, identity)

    if not isinstance(existing, dict):
        # Valid JSON but not an object (e.g. a bare number/list) ⇒ corrupt ⇒ stale.
        return _reclaim(path, pid, port, identity)
    existing_pid = existing.get("pid")
    existing_identity = existing.get("identity", _DEGRADED_IDENTITY)
    # A live, identity-matched holder refuses; anything else is stale ⇒ reclaim.
    # Reject non-PID values before ``os.kill``: ``bool`` is an ``int`` subclass, and
    # 0 / negative would make ``os.kill(pid, 0)`` target a process *group* (never
    # ESRCH) and wedge into a permanent false ``InstanceConflict``.
    if (
        isinstance(existing_pid, int)
        and not isinstance(existing_pid, bool)
        and existing_pid > 0
        and _is_same_live_instance(existing_pid, existing_identity)
    ):
        raise InstanceConflict(
            f"another dev-helper-mcp instance is already running "
            f"(pid={existing_pid}, port={existing.get('port')})",
            details={"pid": existing_pid, "port": existing.get("port")},
        )
    return _reclaim(path, pid, port, identity)


def acquire(port: int) -> LockHandle:
    """Acquire the machine-global single-instance lock for ``port``.

    Creates ``state_dir()`` then atomically creates ``server.lock`` with
    ``O_CREAT|O_EXCL``. On ``EEXIST`` it refuses (``InstanceConflict``) iff a live,
    identity-matched instance holds it, otherwise it reclaims the stale lock.
    """
    path = config.lockfile_path()
    config.state_dir().mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    identity = _identity_token(pid) or _DEGRADED_IDENTITY
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _resolve_existing(path, port, pid, identity)
    try:
        os.write(fd, _payload(pid, port, identity))
    finally:
        os.close(fd)
    logger.info("Acquired single-instance lock (pid=%s, port=%s)", pid, port)
    return LockHandle(path, pid, port, identity)


# ── Port-bind: the authoritative mutex (Task 4) ──


def bind_socket(host: str, port: int) -> socket.socket:
    """Bind a loopback listening socket and return it (ready to hand to uvicorn).

    The bind — not the lockfile — is the authoritative single-instance guarantee:
    ``EADDRINUSE`` ⇒ ``InstanceConflict`` regardless of lock state. Any other
    ``OSError`` propagates. Binding once here and passing the socket to uvicorn
    (Decision B) also closes the probe-then-rebind TOCTOU deferred from Story 1.1.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            raise InstanceConflict(
                f"{host}:{port} is already in use — another dev-helper-mcp instance may be running",
                details={"host": host, "port": port},
            ) from exc
        raise
    return sock
