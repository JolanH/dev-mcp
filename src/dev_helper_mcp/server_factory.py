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

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from .cache import Cache, run_refresher
from .config import APP_NAME, CACHE_REFRESH_INTERVAL, MCP_PATH
from .dashboard.routes import board_route, state_route
from .errors import Internal
from .git.repo_lock import RepoLockRegistry
from .git.runner import GitRunner
from .middleware import OriginValidationMiddleware
from .store import Store
from .tools import handlers
from .tools.handlers import ToolDeps
from .tools.models import (
    CreateTaskIn,
    ListTasksIn,
    ListWorktreesIn,
    RemoveWorktreeIn,
    UpdateTaskIn,
)

logger = logging.getLogger(__name__)


class _DepsHolder:
    """Mutable holder the tool closures read at call time.

    The ``create_task`` tool is registered at build time, but its dependencies
    (asyncio objects + the open DB connection) must be created inside the serving
    loop — so the lifespan populates ``deps`` after build and the closure reads it.
    """

    deps: ToolDeps | None = None


def build_mcp(holder: _DepsHolder) -> FastMCP:
    """Build the FastMCP server with the FINAL 5-tool surface: ``create_task``,
    ``list_worktrees``, ``remove_worktree``, ``update_task`` and ``list_tasks``.

    (Story 1.6 removed the throwaway ``ping`` seed and added the two task tools to lock
    the exactly-5 surface — AC 6.) ``holder.deps`` is populated later by the app
    lifespan; each tool closure reads it at call time (returning a clean ``server not
    ready`` envelope in the startup/teardown window).
    """
    mcp = FastMCP(APP_NAME)

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

    @mcp.tool()
    async def list_worktrees(repo: str | None = None, task_id: str | None = None) -> dict:
        """List worktrees across tracked repos, derived live from git (not a cache).

        Optional ``repo`` / ``task_id`` filters narrow the result. Each entry is
        ``{repo_path, worktree_path, branch, task_id, status, orphaned}``. Returns the
        ``{ok, data, error}`` envelope.
        """
        deps = holder.deps
        if deps is None:
            return {"ok": False, "data": None, "error": Internal("server not ready").as_dict()}

        inp = ListWorktreesIn(repo=repo, task_id=task_id)
        return await handlers.list_worktrees(inp, deps=deps)

    @mcp.tool()
    async def remove_worktree(
        task_id: str,
        repo: str,
        delete_branch: bool = False,
        force: bool = False,
        force_unmerged_branch: bool = False,
    ) -> dict:
        """Remove one task's worktree in ``repo``, guarded (other repos unaffected).

        ``force`` overrides the dirty/locked worktree guard; ``delete_branch`` also
        deletes the ``agent/<task>`` branch, with ``force_unmerged_branch`` overriding
        the unmerged-branch guard. Removing the task's last worktree closes the task.
        Returns the ``{ok, data, error}`` envelope.
        """
        deps = holder.deps
        if deps is None:
            return {"ok": False, "data": None, "error": Internal("server not ready").as_dict()}

        inp = RemoveWorktreeIn(
            task_id=task_id,
            repo=repo,
            delete_branch=delete_branch,
            force=force,
            force_unmerged_branch=force_unmerged_branch,
        )
        return await handlers.remove_worktree(inp, deps=deps)

    @mcp.tool()
    async def update_task(
        task_id: str,
        status: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Update a task's status and/or description (self-report progress).

        ``status`` must be one of the four states: ``running``, ``blocked`` (awaiting
        input), ``review`` (awaiting review), ``done`` (terminal). Any active state can
        move to any of the four; ``done`` is terminal — a done task cannot be
        re-activated (start a new ``create_task`` of the same slug instead). An
        out-of-set value or an illegal transition returns an ``InvalidStatus`` error.
        Setting ``done`` releases the slug for reuse and flags the task closed (its
        worktrees are left untouched). Returns the ``{ok, data, error}`` envelope.
        """
        deps = holder.deps
        if deps is None:
            return {"ok": False, "data": None, "error": Internal("server not ready").as_dict()}

        inp = UpdateTaskIn(task_id=task_id, status=status, description=description)
        return await handlers.update_task(inp, deps=deps)

    @mcp.tool()
    async def list_tasks(status: str | None = None, repo: str | None = None) -> dict:
        """List tasks, optionally filtered by ``status`` or ``repo`` (a Store read).

        Each task is returned with all model fields (``task_id``, ``description``,
        ``status``, ``created_at``, ``updated_at``) plus its per-repo
        ``worktrees: [{repo_path, branch, worktree_path}, …]`` links. A ``repo`` filter
        returns only tasks that touch that repo (links limited to it); empty filters
        mean "no filter". Returns the ``{ok, data, error}`` envelope.
        """
        deps = holder.deps
        if deps is None:
            return {"ok": False, "data": None, "error": Internal("server not ready").as_dict()}

        inp = ListTasksIn(status=status, repo=repo)
        return await handlers.list_tasks(inp, deps=deps)

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
        # them via the holder the tool closures captured. ONE GitRunner per app —
        # its pools are the shared concurrency limiter — handed to BOTH the deps and
        # the Cache (a second runner would split the READ-pool cap).
        store = await Store.open()
        runner = GitRunner()
        cache = Cache(runner=runner, store=store)
        holder.deps = ToolDeps(runner=runner, locks=RepoLockRegistry(), store=store, cache=cache)
        # The warm-up refresh and the refresher task live INSIDE the try so the
        # finally always runs: a startup-cancel (shutdown signal during warm-up, where
        # a git read re-raises CancelledError) must still close the Store and cancel
        # any launched refresher rather than leaking the open connection/task.
        refresher: asyncio.Task[None] | None = None
        try:
            # Warm the cache so /state (Story 2.3) is current the instant the server is
            # up, not blank-until-first-tick.
            await cache.refresh()
            refresher = asyncio.create_task(run_refresher(cache, interval=CACHE_REFRESH_INTERVAL))
            # Load-bearing: run the mounted sub-app's lifespan so the StreamableHTTP
            # session manager starts. Without this, /mcp fails "Task group is not
            # initialized".
            async with mcp_app.router.lifespan_context(mcp_app):
                logger.info("MCP session manager started")
                yield
        finally:
            holder.deps = None
            # Cancel the refresher BEFORE closing the store — the loop's refresh()
            # reads the store; closing it first would hit a closed connection mid-tick.
            if refresher is not None:
                refresher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresher
            await store.close()

    return Starlette(
        # Route ORDER is load-bearing. Mount("/") is a catch-all that owns the whole
        # URL space (the Story 1.1 no-307 wiring: MCP served at streamable_http_path
        # "/mcp" mounted at "/"). Starlette matches routes top-to-bottom and returns
        # the first match, so the explicit /state route MUST precede the Mount or it
        # is shadowed and 404s. This resolves deferred-work.md's "Mount('/') shadows
        # future routes" item via route ordering (the lowest-risk of its three
        # options); /mcp still falls through to the Mount, preserving AC2-of-1.1.
        # Do NOT "tidy" this order. The explicit Route("/") board (Story 2.4a) is also
        # listed before the Mount: "/" resolves to the board, "/mcp" still falls through
        # to the MCP app with no 307.
        routes=[
            state_route(holder),  # explicit GET /state → wins
            board_route(holder),  # explicit GET / board → wins over the catch-all
            Mount("/", app=mcp_app),  # catch-all (keeps /mcp working) → matched last
        ],
        middleware=[Middleware(OriginValidationMiddleware, port=port)],
        lifespan=lifespan,
    )
