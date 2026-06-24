"""Pure parser for ``git worktree list --porcelain`` output (core-adjacent seam).

I/O-free and SDK-free: it takes the raw ``bytes`` stdout of
``git worktree list --porcelain`` and returns parsed records. It does NOT spawn
git (that is ``git/runner.py``'s sole job — Invariant 1) and imports no
``mcp``/``starlette`` (policed by ``tests/test_adapter_seam.py``).

Delimiter-agnostic by design (Story 1.5 decision, 2026-06-23): the default
``--porcelain`` form is newline-delimited with a blank line between worktree
records; the ``-z`` form (git >= 2.36) is the same tokens NUL-terminated with a
bare NUL separating records. We invoke the non-``-z`` form for compatibility with
older git (this machine ships git 2.34, where ``worktree list -z`` errors), but
the parser accepts either so a future ``-z`` switch needs no parser change.

The porcelain record format per worktree:

    worktree <absolute path>
    HEAD <sha>
    branch <ref>        # absent on a detached HEAD
    detached            # bare keyword line (boolean), when detached
    bare / locked [<reason>] / prunable [<reason>]   # optional boolean flags
"""

from __future__ import annotations

from dataclasses import dataclass

_REFS_HEADS = "refs/heads/"


@dataclass(frozen=True)
class WorktreeEntry:
    """One parsed worktree record. ``branch`` is ``None`` for a detached HEAD."""

    path: str
    branch: str | None
    head: str | None
    detached: bool
    locked: bool
    prunable: bool
    bare: bool


def parse_worktree_porcelain(raw: bytes) -> list[WorktreeEntry]:
    """Parse ``git worktree list --porcelain[ -z]`` ``raw`` bytes into records.

    Decodes with ``errors="replace"`` (porcelain paths may carry unicode). Splits
    records on a blank line and fields on the line terminator — auto-detecting the
    ``-z`` NUL form from the presence of a NUL byte. The final record is flushed
    even if git omitted the trailing blank line.
    """
    text = raw.decode(errors="replace")
    sep = "\0" if "\0" in text else "\n"

    entries: list[WorktreeEntry] = []
    current: dict[str, object] = {}
    for line in text.split(sep):
        if line == "":
            # A blank line terminates the current worktree record.
            if current:
                entries.append(_build_entry(current))
                current = {}
            continue
        # ``keyword`` (boolean flag) or ``keyword value`` — split on the FIRST
        # space only so paths containing spaces survive intact.
        keyword, _, value = line.partition(" ")
        current[keyword] = value if value != "" else True

    if current:  # flush a final record with no trailing blank line
        entries.append(_build_entry(current))
    return entries


def _build_entry(fields: dict[str, object]) -> WorktreeEntry:
    branch_ref = fields.get("branch")
    branch: str | None = None
    if isinstance(branch_ref, str):
        branch = branch_ref.removeprefix(_REFS_HEADS)

    head = fields.get("HEAD")
    return WorktreeEntry(
        path=str(fields.get("worktree", "")),
        branch=branch,
        head=head if isinstance(head, str) else None,
        detached="detached" in fields,
        locked="locked" in fields,
        prunable="prunable" in fields,
        bare="bare" in fields,
    )
