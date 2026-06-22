"""SDK adapter: build the mounted, origin-guarded Starlette app.

This module (with ``server``, ``middleware`` and ``cli``) is the ONLY place that
imports ``mcp``/``starlette`` in this story — the SDK-isolation seam (Invariant 7).

Critical wiring (Invariant 8, verified against python-sdk #1168):
- Clients reach ``/mcp`` with NO 307 redirect.
- The app-owned lifespan MUST wrap the mounted sub-app's lifespan, otherwise the
  StreamableHTTP session manager never starts and every ``/mcp`` request fails
  with "Task group is not initialized". Starlette does not auto-run a mounted
  sub-app's lifespan.
- Our Origin middleware is the OUTERMOST layer on the parent app.

No-307 wiring note (deviation from the story's literal pseudo-code, same intent):
The story sketched ``streamable_http_path="/" + Mount("/mcp")``. On the resolved
Starlette (1.3.x), that combination 307-redirects a bare ``/mcp`` to ``/mcp/``
(the mount strips ``/mcp`` to ``""`` while the inner route is ``/``), which fails
AC 2's "no 307" requirement because the MCP SDK client does not follow redirects
on POST. The equivalent wiring that serves a bare ``/mcp`` with a clean 200 is
``streamable_http_path="/mcp"`` (the FastMCP default) mounted at ``Mount("/")``.
All three invariants still hold: no 307, ``/mcp`` reachable, lifespan wrapped,
Origin middleware outermost.
"""

import contextlib
import logging
from collections.abc import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from .config import APP_NAME, MCP_PATH
from .middleware import OriginValidationMiddleware
from .util import now_iso

logger = logging.getLogger(__name__)


def build_mcp() -> FastMCP:
    """Build the FastMCP server with the seed no-op ``ping`` tool registered."""
    mcp = FastMCP(APP_NAME)

    @mcp.tool()
    def ping() -> dict:
        """Trivial health/liveness tool seeding the {ok, data, error} envelope."""
        return {"ok": True, "data": {"pong": True, "time": now_iso()}, "error": None}

    # Serve the streamable-HTTP endpoint at /mcp directly so a bare /mcp resolves
    # with no trailing-slash 307 redirect (see module docstring).
    mcp.settings.streamable_http_path = MCP_PATH
    return mcp


def create_app(port: int) -> Starlette:
    """Build the parent Starlette app bound to ``port``.

    ``port`` is baked into the Origin allowlist (it is only known after the
    free-port scan), so this factory must be called with the resolved port.
    """
    mcp = build_mcp()
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # Load-bearing: run the mounted sub-app's lifespan so the StreamableHTTP
        # session manager starts. Without this, /mcp fails "Task group is not
        # initialized".
        async with mcp_app.router.lifespan_context(mcp_app):
            logger.info("MCP session manager started")
            yield

    return Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=[Middleware(OriginValidationMiddleware, port=port)],
        lifespan=lifespan,
    )
