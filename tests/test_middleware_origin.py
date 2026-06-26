"""AC 3: Origin-validation middleware (present+bad -> 403, absent/allowed -> pass).

Asserted on /mcp AND a non-/mcp route to prove the middleware is the outermost
layer over every route, not just the MCP mount.
"""

import asyncio

import httpx
import pytest

from dev_helper_mcp.config import DEFAULT_PORT

ALLOWED_ORIGIN = f"http://127.0.0.1:{DEFAULT_PORT}"
ALLOWED_ORIGIN_LOCALHOST = f"http://localhost:{DEFAULT_PORT}"
BAD_ORIGIN = "http://evil.example.com"

# "/" is a non-/mcp route (404 when allowed). "/state" (Story 2.3) is a real
# dashboard route guarded by the SAME outermost middleware as /mcp. The point is
# the Origin gate runs on every route regardless of whether it resolves.
ROUTES = ["/mcp", "/", "/state"]


async def _request(app, asgi_client_factory, path: str, origin: str | None) -> httpx.Response:
    headers = {} if origin is None else {"origin": origin}
    # Drive the lifespan so allowed/absent requests get clean responses rather
    # than an un-started-session-manager 500.
    async with app.router.lifespan_context(app):
        async with asgi_client_factory() as client:
            return await client.get(path, headers=headers)


@pytest.mark.parametrize("path", ROUTES)
def test_bad_origin_rejected(app, asgi_client_factory, path):
    resp = asyncio.run(_request(app, asgi_client_factory, path, BAD_ORIGIN))
    assert resp.status_code == 403


@pytest.mark.parametrize("path", ROUTES)
def test_absent_origin_allowed(app, asgi_client_factory, path):
    resp = asyncio.run(_request(app, asgi_client_factory, path, None))
    # Allowed = the Origin gate did not block it (any non-403 outcome is fine).
    assert resp.status_code != 403


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("origin", [ALLOWED_ORIGIN, ALLOWED_ORIGIN_LOCALHOST])
def test_allowlisted_origin_allowed(app, asgi_client_factory, path, origin):
    resp = asyncio.run(_request(app, asgi_client_factory, path, origin))
    assert resp.status_code != 403
