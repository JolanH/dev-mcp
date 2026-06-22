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
from .errors import Internal
from .git.repo_lock import RepoLockRegistry
from .git.runner import GitRunner
from .middleware import OriginValidationMiddleware
from .store import Store
from .tools import handlers
from .tools.handlers import ToolDeps
from .tools.models import CreateTaskIn
from .util import now_iso

logger = logging.getLogger(__name__)


class _DepsHolder:
    """Mutable holder the tool closures read at call time.

    The ``create_task`` tool is registered at build time, but its dependencies
    (asyncio objects + the open DB connection) must be created inside the serving
    loop — so the lifespan populates ``deps`` after build and the closure reads it.
    """

    deps: ToolDeps | None = None


def build_mcp(holder: _DepsHolder) -> FastMCP:
    """Build the FastMCP server with the ``ping`` and ``create_task`` tools.

    ``holder.deps`` is populated later by the app lifespan; the ``create_task``
    closure reads it at call time.
    """
    mcp = FastMCP(APP_NAME)

    @mcp.tool()
    def ping() -> dict:
        """Trivial health/liveness tool seeding the {ok, data, error} envelope."""
        return {"ok": True, "data": {"pong": True, "time": now_iso()}, "error": None}

    @mcp.tool()
    async def create_task(
        task_name: str,
        description: str,
        repos: list[str],
        base_ref: str | None = None,
    ) -> dict:
        """Create a task spanning one or more repos.

        Each repo gets an isolated worktree at ``<repo>.worktrees/<task>/`` on a
        new ``agent/<task>`` branch. Returns the ``{ok, data, error}`` envelope.
        """
        # The lifespan populates deps before yielding and nulls them on shutdown;
        # guard the teardown/startup window so a late request returns a clean
        # not-ready envelope instead of dereferencing None into an opaque Internal.
        deps = holder.deps
        if deps is None:
            return {"ok": False, "data": None, "error": Internal("server not ready").as_dict()}

        inp = CreateTaskIn(
            task_name=task_name, description=description, repos=repos, base_ref=base_ref
        )
        return await handlers.create_task(inp, deps=deps)

    # Serve the streamable-HTTP endpoint at /mcp directly so a bare /mcp resolves
    # with no trailing-slash 307 redirect (see module docstring).
    mcp.settings.streamable_http_path = MCP_PATH
    return mcp


def create_app(port: int) -> Starlette:
    """Build the parent Starlette app bound to ``port``.

    ``port`` is baked into the Origin allowlist (it is only known after the
    free-port scan), so this factory must be called with the resolved port.
    """
    holder = _DepsHolder()
    mcp = build_mcp(holder)
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # Build the shared tool deps INSIDE the running loop (asyncio primitives +
        # the open aiosqlite connection belong to the serving loop), then expose
        # them via the holder the tool closures captured.
        store = await Store.open()
        holder.deps = ToolDeps(runner=GitRunner(), locks=RepoLockRegistry(), store=store)
        try:
            # Load-bearing: run the mounted sub-app's lifespan so the StreamableHTTP
            # session manager starts. Without this, /mcp fails "Task group is not
            # initialized".
            async with mcp_app.router.lifespan_context(mcp_app):
                logger.info("MCP session manager started")
                yield
        finally:
            holder.deps = None
            await store.close()

    return Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=[Middleware(OriginValidationMiddleware, port=port)],
        lifespan=lifespan,
    )
