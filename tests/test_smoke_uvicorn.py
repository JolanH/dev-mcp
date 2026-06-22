"""AC 4: real-port smoke test — the bound socket is 127.0.0.1, never 0.0.0.0.

Exactly one real-port test in the suite; ``slow``-marked so the pre-commit gate
can run the fast suite by default.
"""

import threading
import time

import pytest
import uvicorn

from dev_helper_mcp.config import BIND_HOST
from dev_helper_mcp.server import find_free_port
from dev_helper_mcp.server_factory import create_app


@pytest.mark.slow
def test_bound_socket_is_loopback():
    port = find_free_port()
    app = create_app(port)
    config = uvicorn.Config(app, host=BIND_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        # Wait for the server to actually bind its sockets.
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn failed to start within timeout"

        bound_hosts = [sock.getsockname()[0] for sock in server.servers[0].sockets]
        assert bound_hosts, "no bound sockets found"
        for host in bound_hosts:
            assert host == "127.0.0.1", f"bound to {host}, expected 127.0.0.1"
            assert host != "0.0.0.0", "must never bind 0.0.0.0"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
