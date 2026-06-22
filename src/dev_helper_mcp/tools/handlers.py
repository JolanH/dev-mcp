"""MCP tool handlers (adapter layer) — convert core results/errors to the envelope.

A handler unpacks its ``*In`` model into plain args for the SDK-free core, then
wraps the outcome in the uniform ``{ok, data, error}`` envelope (matching the
``ping`` seed exactly). A typed ``DevHelperError`` becomes ``{ok:false, error:…}``;
any unexpected exception collapses to ``Internal`` — a stack trace never leaks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core import tasks
from ..errors import DevHelperError, Internal
from ..git.repo_lock import RepoLockRegistry
from ..git.runner import GitRunner
from ..store import Store
from .models import CreateTaskIn

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
