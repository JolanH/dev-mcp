"""Typed error taxonomy (core layer — no ``mcp``/``starlette`` imports).

Core logic raises a :class:`DevHelperError` subclass; the adapter layer
(``tools/handlers.py``, Story 1.3+) catches it and converts it to the
``{ok: false, error: {code, message, details}}`` envelope. This module only
*defines* the taxonomy — it does not build the envelope.

``code`` is a stable contract (agents branch on it); ``message`` may change.
"""

from __future__ import annotations


class DevHelperError(Exception):
    """Base for every typed domain error.

    Subclasses set their own stable ``code``. The base default ``Internal`` is
    also the catch-all the adapter uses for unexpected (non-``DevHelperError``)
    exceptions so a tool never leaks a raw stack trace.
    """

    code: str = "Internal"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict = details if details is not None else {}

    def as_dict(self) -> dict:
        """Serialize to the envelope ``error`` payload."""
        return {"code": self.code, "message": self.message, "details": self.details}


# ── The complete, stable taxonomy. Later stories only *raise* these. ──


class BranchExists(DevHelperError):
    code = "BranchExists"


class WorktreePathInUse(DevHelperError):
    code = "WorktreePathInUse"


class BaseRefNotFound(DevHelperError):
    code = "BaseRefNotFound"


class DirtyWorktree(DevHelperError):
    code = "DirtyWorktree"


class UnmergedBranch(DevHelperError):
    code = "UnmergedBranch"


class TaskNotFound(DevHelperError):
    code = "TaskNotFound"


class ActiveTaskConflict(DevHelperError):
    code = "ActiveTaskConflict"


class LockedWorktree(DevHelperError):
    code = "LockedWorktree"


class InvalidTaskName(DevHelperError):
    code = "InvalidTaskName"


class GitTimeout(DevHelperError):
    code = "GitTimeout"


class InstanceConflict(DevHelperError):
    code = "InstanceConflict"


class NotAGitRepo(DevHelperError):
    code = "NotAGitRepo"


class RollbackIncomplete(DevHelperError):
    code = "RollbackIncomplete"


class PortUnavailable(DevHelperError):
    code = "PortUnavailable"


class Internal(DevHelperError):
    code = "Internal"
