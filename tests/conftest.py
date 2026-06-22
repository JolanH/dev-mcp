"""Shared test fixtures.

The baseline harness is an in-process ``httpx.ASGITransport`` client against the
parent Starlette app — no real port, no sockets. Async tests are driven with
``asyncio.run`` so no pytest-asyncio plugin is required (dev deps are kept to
ruff + pytest + httpx per the story).

The single real-port uvicorn smoke test lives in ``test_smoke_uvicorn.py`` and
is ``slow``-marked.
"""

import httpx
import pytest

from dev_helper_mcp.config import DEFAULT_PORT
from dev_helper_mcp.server_factory import create_app

# Port baked into the app + the Origin allowlist for the in-process tests.
TEST_PORT = DEFAULT_PORT

# In-process base URL. Uses 127.0.0.1:<port> (not the httpx default "testserver")
# so the synthesised Host header passes FastMCP's own host validation.
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture
def base_url() -> str:
    return BASE_URL


@pytest.fixture
def app():
    """The parent Starlette app bound to TEST_PORT."""
    return create_app(TEST_PORT)


@pytest.fixture
def asgi_client_factory(app):
    """Return an httpx.AsyncClient factory talking to ``app`` in-process.

    Shaped as an ``McpHttpClientFactory`` so it can also be handed to the MCP
    SDK's ``streamablehttp_client``.
    """

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            # raise_app_exceptions=False so an un-started session manager (when a
            # test deliberately skips the lifespan) surfaces as a 5xx response
            # rather than a raised exception.
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=BASE_URL,
            headers=headers,
            timeout=timeout if timeout is not None else httpx.Timeout(30.0),
            auth=auth,
        )

    return factory
