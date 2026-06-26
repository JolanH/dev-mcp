"""Server lifecycle: bind 127.0.0.1 once, acquire the single-instance lock, run
uvicorn, release on shutdown.

Part of the adapter seam (imports uvicorn). Story 3.1 added the machine-global
single-instance lockfile (``lock.py``) wired around the existing run: the socket
is bound ONCE here and handed to uvicorn (Decision B — closing the Story 1.1
probe-then-rebind TOCTOU), the lock is acquired with the bound port, and released
on clean shutdown. Strict ``--port`` / ``PortUnavailable`` and the ``stop`` /
``--release-lock`` CLI are Story 3.2.
"""

import atexit
import logging
import os
import signal
import socket

import uvicorn

from . import lock
from .config import BIND_HOST, PORT_RANGE
from .errors import InstanceConflict
from .server_factory import create_app

logger = logging.getLogger(__name__)


def _port_free(host: str, port: int) -> bool:
    """Return True if ``port`` is a valid, bindable port on ``host`` right now.

    Port 0 (OS-assigned ephemeral) and out-of-range values are treated as
    not-free so they never become a forced bind target.
    """
    if not (0 < port <= 65535):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def find_free_port(host: str = BIND_HOST, port_range: range = PORT_RANGE) -> int:
    """Return the first free port in ``port_range`` on ``host``.

    Raises ``RuntimeError`` if every port in the range is occupied.
    """
    for port in port_range:
        if _port_free(host, port):
            return port
    raise RuntimeError(
        f"No free port available in range {port_range.start}-{port_range.stop - 1} on {host}"
    )


def _bind_scanning(host: str, port_range: range) -> socket.socket:
    """Bind and RETURN a listening socket on the first free port in ``port_range``.

    Binds once (Decision B): the returned socket is handed straight to uvicorn, so
    there is no probe-close-then-rebind gap. A per-port ``InstanceConflict``
    (``EADDRINUSE``) just means "occupied" during the scan — try the next port.
    """
    for port in port_range:
        try:
            return lock.bind_socket(host, port)
        except InstanceConflict:
            continue
    raise RuntimeError(
        f"No free port available in range {port_range.start}-{port_range.stop - 1} on {host}"
    )


def _install_release(handle: lock.LockHandle) -> None:
    """Ensure the lock is released on clean shutdown.

    ``atexit`` is the primary backstop (covers uvicorn's graceful return + normal
    exit); signal handlers cover SIGTERM/SIGINT arriving in the pre-serve window
    before uvicorn installs its own. ``release()`` is idempotent + owned-guarded,
    so overlapping triggers are safe. ``kill -9`` is intentionally NOT covered — it
    leaves a stale lock that the next start reclaims.
    """
    atexit.register(handle.release)

    def _handler(signum: int, _frame: object) -> None:
        handle.release()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except ValueError:
            # Not in the main thread (e.g. the slow smoke test) — atexit still covers it.
            pass


def run(port: int | None = None) -> None:
    """Bind 127.0.0.1 once, acquire the single-instance lock, run the server.

    The bound socket is the authoritative single-instance mutex; the lockfile is
    the diagnostic/fast-path guard (it also catches a live instance on a *different*
    port). The lock is released on clean shutdown via ``atexit``/signal/``finally``.
    """
    if port is None:
        sock = _bind_scanning(BIND_HOST, PORT_RANGE)
    else:
        try:
            sock = lock.bind_socket(BIND_HOST, port)
        except InstanceConflict:
            # 3.1 keeps the Story 1.1 scan fallback; strict --port is Story 3.2.
            logger.warning(
                "Requested port %s is unavailable; scanning %d-%d for a free port",
                port,
                PORT_RANGE.start,
                PORT_RANGE.stop - 1,
            )
            sock = _bind_scanning(BIND_HOST, PORT_RANGE)

    bound_port = sock.getsockname()[1]
    try:
        handle = lock.acquire(bound_port)
    except BaseException:
        # A live instance holds the lock (possibly on another port) — drop our bind.
        sock.close()
        raise

    _install_release(handle)
    try:
        app = create_app(bound_port)
        dashboard_url = f"http://{BIND_HOST}:{bound_port}/"
        # Printed to stdout so the operator sees where to connect on startup.
        print(f"dev-helper-mcp listening — dashboard: {dashboard_url}", flush=True)
        logger.info("Binding %s:%d", BIND_HOST, bound_port)
        config = uvicorn.Config(app, host=BIND_HOST, port=bound_port, log_level="info")
        server = uvicorn.Server(config)
        # Hand uvicorn the already-bound socket (Decision B) — it does not re-bind.
        server.run(sockets=[sock])
    finally:
        # Covers app/uvicorn construction too: any failure after the bind must not
        # leak the listening socket or leave the lock held. close()/release() are
        # idempotent, so uvicorn's own socket close + atexit/signal are harmless dups.
        sock.close()
        handle.release()
