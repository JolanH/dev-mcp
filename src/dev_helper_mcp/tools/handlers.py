"""MCP tool handlers (adapter layer) — convert core results/errors to the envelope.

A handler unpacks its ``*In`` model into plain args for the SDK-free core, then
wraps the outcome in the uniform ``{ok, data, error}`` envelope (matching the
``ping`` seed exactly). A typed ``DevHelperError`` becomes ``{ok:false, error:…}``;
any unexpected exception collapses to ``Internal`` — a stack trace never leaks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..cache import Cache
from ..core import tasks, worktrees
from ..errors import DevHelperError, Internal
from ..git.repo_lock import RepoLockRegistry
from ..git.runner import GitRunner
from ..store import Store
from .models import (
    CreateTaskIn,
    ListTasksIn,
    ListWorktreesIn,
    RemoveWorktreeIn,
    UpdateTaskIn,
)

logger = logging.getLogger(__name__)


@dataclass
class ToolDeps:
    """Shared, loop-bound dependencies a tool handler needs.

    Constructed by the server lifespan inside the running loop (asyncio objects +
    the open DB connection belong to the serving loop) and captured by the tool
    closures registered at build time.
    """

    runner: GitRunner
    locks: RepoLockRegistry
    store: Store
    cache: Cache


async def create_task(inp: CreateTaskIn, *, deps: ToolDeps) -> dict:
    """Handle ``create_task``: resolve cwd-derived defaults, run the core orchestrator,
    return the envelope.

    Only ``task_name`` is required. When ``repos`` is omitted it defaults to the git repo
    containing the server's current directory; when ``base_ref`` is omitted it defaults
    to that directory's current branch. A default that cannot be resolved surfaces a
    typed ``NoDefaultRepo`` / ``NoDefaultBaseRef`` as error-as-data (inside the try, so it
    becomes the ``{ok:false, error:…}`` envelope like any other guard).
    """
    try:
        repos = inp.repos
        if not repos:
            repos = [await tasks.resolve_default_repo(runner=deps.runner)]
        base_ref = inp.base_ref
        if base_ref is None:
            base_ref = await tasks.resolve_default_base_ref(runner=deps.runner)
        data = await tasks.create(
            inp.task_name,
            inp.description,
            repos,
            base_ref=base_ref,
            runner=deps.runner,
            locks=deps.locks,
            store=deps.store,
        )
        # Refresh the shared cache on the just-committed git-derived state BEFORE
        # returning, so a tool never reports ok on stale state (AC2). refresh() is
        # total — it cannot turn this successful mutation into a failure. The
        # returned ``data`` is unchanged; the refresh is a side effect on the cache.
        await deps.cache.refresh()
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
        # Diagnosable from stderr without leaking user content: log the stable
        # error.code only — never the description/annotation body (NFR-7).
        logger.info("create_task failed: %s", exc.code)
        return {"ok": False, "data": None, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001 — never leak a stack trace through the tool
        logger.exception("unexpected error in create_task")
        return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}


async def list_worktrees(inp: ListWorktreesIn, *, deps: ToolDeps) -> dict:
    """Handle ``list_worktrees``: live-derive the worktree view, return the envelope."""
    try:
        data = await worktrees.list_worktrees(
            repo=inp.repo,
            task_id=inp.task_id,
            runner=deps.runner,
            store=deps.store,
        )
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
        logger.info("list_worktrees failed: %s", exc.code)
        return {"ok": False, "data": None, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001 — never leak a stack trace through the tool
        logger.exception("unexpected error in list_worktrees")
        return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}


async def remove_worktree(inp: RemoveWorktreeIn, *, deps: ToolDeps) -> dict:
    """Handle ``remove_worktree``: run the guarded removal, return the envelope."""
    try:
        data = await worktrees.remove_worktree(
            inp.task_id,
            inp.repo,
            delete_branch=inp.delete_branch,
            force=inp.force,
            force_unmerged_branch=inp.force_unmerged_branch,
            runner=deps.runner,
            locks=deps.locks,
            store=deps.store,
        )
        # Refresh after the committed mutation, before returning (AC2). Total.
        await deps.cache.refresh()
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
        logger.info("remove_worktree failed: %s", exc.code)
        return {"ok": False, "data": None, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001 — never leak a stack trace through the tool
        logger.exception("unexpected error in remove_worktree")
        return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}


async def update_task(inp: UpdateTaskIn, *, deps: ToolDeps) -> dict:
    """Handle ``update_task``: run the core status/description update, return the envelope."""
    try:
        data = await tasks.update_task(
            inp.task_id,
            status=inp.status,
            description=inp.description,
            store=deps.store,
        )
        # Refresh after the committed mutation, before returning (AC2). Total.
        await deps.cache.refresh()
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
        logger.info("update_task failed: %s", exc.code)
        return {"ok": False, "data": None, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001 — never leak a stack trace through the tool
        logger.exception("unexpected error in update_task")
        return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}


async def list_tasks(inp: ListTasksIn, *, deps: ToolDeps) -> dict:
    """Handle ``list_tasks``: read the filtered task view from the store, return the envelope."""
    try:
        data = await tasks.list_tasks(status=inp.status, repo=inp.repo, store=deps.store)
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
        logger.info("list_tasks failed: %s", exc.code)
        return {"ok": False, "data": None, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001 — never leak a stack trace through the tool
        logger.exception("unexpected error in list_tasks")
        return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}
