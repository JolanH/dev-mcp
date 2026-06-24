"""MCP tool handlers (adapter layer) — convert core results/errors to the envelope.

A handler unpacks its ``*In`` model into plain args for the SDK-free core, then
wraps the outcome in the uniform ``{ok, data, error}`` envelope (matching the
``ping`` seed exactly). A typed ``DevHelperError`` becomes ``{ok:false, error:…}``;
any unexpected exception collapses to ``Internal`` — a stack trace never leaks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core import tasks, worktrees
from ..errors import DevHelperError, Internal
from ..git.repo_lock import RepoLockRegistry
from ..git.runner import GitRunner
from ..store import Store
from .models import CreateTaskIn, ListWorktreesIn, RemoveWorktreeIn

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


async def create_task(inp: CreateTaskIn, *, deps: ToolDeps) -> dict:
    """Handle ``create_task``: run the core orchestrator, return the envelope."""
    try:
        data = await tasks.create(
            inp.task_name,
            inp.description,
            inp.repos,
            base_ref=inp.base_ref,
            runner=deps.runner,
            locks=deps.locks,
            store=deps.store,
        )
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
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
        return {"ok": True, "data": data, "error": None}
    except DevHelperError as exc:
        return {"ok": False, "data": None, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001 — never leak a stack trace through the tool
        logger.exception("unexpected error in remove_worktree")
        return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}
