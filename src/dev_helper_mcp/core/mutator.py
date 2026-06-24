"""``RepoMutator`` seam — injectable per-repo worktree create/teardown primitives.

Story 1.4 extracts the two destructive git ops behind a small SDK-free seam so the
cross-repo rollback matrix can be fault-injected deterministically: a ``FlakyMutator``
in tests fails a chosen ``add``/``remove`` *in-process* (raising a synthetic typed
error) without corrupting git state, so the orchestrator's compensation still runs
**real** git on **real** tmp worktrees. Production uses :class:`GitRepoMutator`, which
routes every git call through the injected :class:`GitRunner` on the **mutation** pool
— never ``subprocess``/``os.system`` (Invariant 1).

Core-layer module: imports no ``mcp``/``starlette`` (policed by
``tests/test_adapter_seam.py``).

*Reviewer note:* this seeds ``core/mutator.py`` (not ``core/worktrees.py``); Story 1.5
may extend it with the user-facing ``list``/``remove_worktree`` surface, or move the
seam — either is in-spirit.
"""

from __future__ import annotations

from typing import Protocol

from ..errors import BranchExists, DevHelperError, Internal, WorktreePathInUse
from ..git.runner import GitRunner, Pool


class RepoMutator(Protocol):
    """The injectable create/teardown primitives ``core.tasks.create`` provisions through.

    Both methods are repo-targeted (the ``repo`` is the canonical absolute path the
    orchestrator locks on) and raise a typed :class:`DevHelperError` on failure so the
    orchestrator can drive reverse-order compensation and detect a failed teardown.
    """

    async def add(self, repo: str, branch: str, worktree_path: str, start_point: str) -> None:
        """Create ``branch`` + a worktree at ``worktree_path`` from ``start_point``."""
        ...

    async def remove(self, repo: str, branch: str, worktree_path: str) -> None:
        """Compensation primitive: remove the worktree, then force-delete ``branch``."""
        ...


def _classify_add_failure(
    repo: str, branch: str, worktree_path: str, stderr: bytes
) -> DevHelperError:
    """Best-effort typed classification of a non-zero ``git worktree add`` (AC-1).

    Closes Story 1.3's deferral (collisions surfaced as a generic ``Internal``). Maps
    git's stderr to ``BranchExists`` / ``WorktreePathInUse`` where unambiguous; an
    ``Internal`` carrying the trimmed stderr is the accepted fallback — the typed code
    is a nice-to-have, the compensation is the hard requirement.
    """
    text = stderr.decode(errors="replace").strip()
    low = text.lower()
    # `fatal: a branch named '<b>' already exists` / `fatal: '<b>' is already checked
    # out at '<path>'` → the branch is the obstruction.
    if "a branch named" in low or "is already checked out" in low:
        return BranchExists(
            "branch already exists",
            {"repo": repo, "branch": branch, "stderr": text},
        )
    # `fatal: '<path>' already exists` → the target worktree path is the obstruction.
    if "already exists" in low:
        return WorktreePathInUse(
            "worktree path already in use",
            {"repo": repo, "worktree_path": worktree_path, "stderr": text},
        )
    return Internal(
        "git worktree add failed",
        {"repo": repo, "branch": branch, "stderr": text},
    )


class GitRepoMutator:
    """Production :class:`RepoMutator`: every git op via the injected ``runner``.

    Holds the injected :class:`GitRunner` (it does NOT construct one — asyncio objects
    live in the running loop, Invariant from 1.2). Both ops run on the **mutation**
    pool while the orchestrator holds the per-repo mutex; the destructive teardown
    ops are NEVER on the read/refresh path (project-context anti-pattern).
    """

    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    async def add(self, repo: str, branch: str, worktree_path: str, start_point: str) -> None:
        """``git worktree add -b <branch> <path> <start> --``; raise typed on non-zero.

        Byte-for-byte the command Story 1.3 ran inline (same ``--`` end-of-options,
        same MUTATION pool) so its green tests keep passing.
        """
        result = await self._runner.run_git(
            repo,
            ["worktree", "add", "-b", branch, worktree_path, start_point, "--"],
            pool=Pool.MUTATION,
        )
        if result.returncode != 0:
            raise _classify_add_failure(repo, branch, worktree_path, result.stderr)

    async def remove(self, repo: str, branch: str, worktree_path: str) -> None:
        """Tear down a just-created worktree: ``worktree remove --force`` then ``branch -D``.

        Order matters — the worktree must go first (git refuses to delete a branch
        checked out in a worktree); the branch is brand-new + unmerged so ``-D``
        (force) is correct and safe (this very call created it). Raises ``Internal``
        if either op returns non-zero so the orchestrator can mark this repo orphaned
        (AC-3).
        """
        rm = await self._runner.run_git(
            repo, ["worktree", "remove", "--force", worktree_path], pool=Pool.MUTATION
        )
        if rm.returncode != 0:
            raise Internal(
                "git worktree remove failed",
                {
                    "repo": repo,
                    "worktree_path": worktree_path,
                    "stderr": rm.stderr.decode(errors="replace").strip(),
                },
            )
        br = await self._runner.run_git(repo, ["branch", "-D", branch], pool=Pool.MUTATION)
        if br.returncode != 0:
            raise Internal(
                "git branch -D failed",
                {
                    "repo": repo,
                    "branch": branch,
                    "stderr": br.stderr.decode(errors="replace").strip(),
                },
            )
