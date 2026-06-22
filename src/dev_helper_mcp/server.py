"""Server lifecycle: pick a free port, bind 127.0.0.1, run uvicorn.

Part of the adapter seam (imports uvicorn). Port *scanning* for the first free
port is in scope here; the single-instance lockfile and ``stop`` are Story 3.1+.
"""

import logging
import socket

import uvicorn

from .config import BIND_HOST, PORT_RANGE
from .server_factory import create_app

logger = logging.getLogger(__name__)


def find_free_port(host: str = BIND_HOST, port_range: range = PORT_RANGE) -> int:
    """Return the first free port in ``port_range`` on ``host``.

    Raises ``RuntimeError`` if every port in the range is occupied.
    """
    for port in port_range:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f"No free port available in range {port_range.start}-{port_range.stop - 1} on {host}"
    )


def run(port: int | None = None) -> None:
    """Bind 127.0.0.1 on a free port, print the dashboard URL, run the server."""
    bound_port = port if port is not None else find_free_port()
    app = create_app(bound_port)
    dashboard_url = f"http://{BIND_HOST}:{bound_port}/"
    # Printed to stdout so the operator sees where to connect on startup.
    print(f"dev-helper-mcp listening — dashboard: {dashboard_url}", flush=True)
    logger.info("Binding %s:%d", BIND_HOST, bound_port)
    uvicorn.run(app, host=BIND_HOST, port=bound_port, log_level="info")
