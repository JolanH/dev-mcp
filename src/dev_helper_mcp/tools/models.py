"""Pydantic ``*In`` boundary models for the MCP tools (adapter layer).

These live ONLY here, at the tool boundary. Core functions (``core.tasks.create``)
take plain typed args, never the Pydantic model — the model is unpacked in the
handler. Importing pydantic here is allowed (this layer is not seam-scanned).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateTaskIn(BaseModel):
    """Validated input for the ``create_task`` tool."""

    task_name: str
    description: str
    repos: list[str] = Field(min_length=1)
    base_ref: str | None = None


class ListWorktreesIn(BaseModel):
    """Validated input for the ``list_worktrees`` tool (both filters optional)."""

    repo: str | None = None
    task_id: str | None = None


class RemoveWorktreeIn(BaseModel):
    """Validated input for the ``remove_worktree`` tool.

    Two distinct guard-override flags, never conflated: ``force`` overrides the
    dirty/locked *worktree* guard; ``force_unmerged_branch`` overrides the unmerged
    *branch* guard (only relevant when ``delete_branch`` is set).
    """

    task_id: str
    repo: str
    delete_branch: bool = False
    force: bool = False
    force_unmerged_branch: bool = False
