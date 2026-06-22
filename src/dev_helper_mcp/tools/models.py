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
