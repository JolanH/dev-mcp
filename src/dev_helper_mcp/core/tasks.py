"""``create_task`` orchestrator (core layer — no ``mcp``/``starlette`` import).

Builds a multi-repo task: one isolated worktree + ``agent/<slug>`` branch per
repo, all-or-nothing. Dependencies (``GitRunner``, ``RepoLockRegistry``, ``Store``)
are *injected* — core never constructs the runner/store (testability + the
"asyncio objects live in the running loop" rule). Returns the plain success
``data`` dict; raises a typed :class:`DevHelperError` on any guard (the adapter
converts it to the ``{ok, data, error}`` envelope).

Scope (Story 1.3): happy path + preflight rejection only. Every error here is
raised in **preflight, before any git mutation**, so success and rejection both
leave the system clean — no compensation. Post-preflight rollback is Story 1.4.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import branch_name_for, worktree_path_for
from ..core.slug import slugify
from ..errors import (
    ActiveTaskConflict,
    BaseRefNotFound,
    BranchExists,
    InvalidTaskName,
    Internal,
    WorktreePathInUse,
)
from ..git.repo_lock import RepoLockRegistry
from ..git.runner import GitRunner, Pool
from ..store import Store
from ..util import now_iso

_RUNNING = "running"


async def create(
    task_name: str,
    description: str,
    repos: list[str],
    *,
    base_ref: str | None = None,
    runner: GitRunner,
    locks: RepoLockRegistry,
    store: Store,
) -> dict:
    """Create the task across every repo in ``repos``; return the success ``data``.

    Raises ``InvalidTaskName``/``NotAGitRepo``/``BranchExists``/``WorktreePathInUse``
    /``BaseRefNotFound``/``ActiveTaskConflict`` in preflight (nothing mutated), or a
    typed git/``Internal`` error if a mutation fails after preflight.
    """
    slug = slugify(task_name)  # raises InvalidTaskName
    branch = branch_name_for(slug)

    if not repos:
        raise InvalidTaskName("at least one repo is required", {"repos": repos})

    # Normalize → dedup → canonical (sorted-abspath) order. Sorted order is
    # MANDATORY: it prevents the [A,B]/[B,A] lock-ordering deadlock between two
    # concurrent creates with overlapping repos (Invariant 12).
    canonical = sorted({os.path.abspath(r) for r in repos})

    acquired: list = []
    try:
        for repo in canonical:  # acquire in sorted order
            lock = locks.lock_for(repo)
            await lock.acquire()
            acquired.append(lock)

        # ── Active-slug conflict gate (AC 4) — before any mutation. ──
        # "active" is literally `status != 'done'` (running/blocked/review all
        # conflict); never an enumerated allowlist.
        existing = await store.get_task(slug)
        if existing is not None and existing["status"] != "done":
            raise ActiveTaskConflict(
                "task slug already in use by an active task",
                {"task_id": slug, "status": existing["status"]},
            )

        # ── Per-repo preflight (AC 5) — raise on the FIRST collision, mutate nothing. ──
        plan: list[tuple[str, Path]] = []
        for repo in canonical:
            await runner.require_git_repo(repo)  # raises NotAGitRepo

            head_ref = await runner.run_git(
                repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], pool=Pool.READ
            )
            if head_ref.returncode == 0:
                raise BranchExists("branch already exists", {"repo": repo, "branch": branch})

            wt_path = worktree_path_for(Path(repo), slug)
            # lexists (not exists): a broken symlink at the path must still count
            # as "in use" — exists() follows the link and reports False, letting
            # the collision slip past preflight and fail later as a generic Internal.
            if os.path.lexists(wt_path):
                raise WorktreePathInUse(
                    "worktree path already in use",
                    {"repo": repo, "worktree_path": str(wt_path)},
                )

            if base_ref is not None:
                ref_check = await runner.run_git(
                    repo,
                    ["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"],
                    pool=Pool.READ,
                )
                if ref_check.returncode != 0:
                    raise BaseRefNotFound(
                        "base_ref not found in repo", {"repo": repo, "base_ref": base_ref}
                    )

            plan.append((repo, wt_path))

        # ── Provisioning (AC 1, 2, 3) — preflight passed; create branch+worktree per repo. ──
        start_point = base_ref or "HEAD"
        worktrees: list[tuple[str, str, str]] = []
        for repo, wt_path in plan:
            result = await runner.run_git(
                repo,
                ["worktree", "add", "-b", branch, str(wt_path), start_point, "--"],
                pool=Pool.MUTATION,
            )
            if result.returncode != 0:
                # Post-preflight failure (race/disk). Surface it typed; full
                # reverse-order compensation is Story 1.4, not this story.
                raise Internal(
                    "git worktree add failed",
                    {
                        "repo": repo,
                        "branch": branch,
                        "stderr": result.stderr.decode(errors="replace").strip(),
                    },
                )
            worktrees.append((repo, branch, str(wt_path)))

        # ── Persist last, in one transaction (AC 2). ──
        ts = now_iso()
        # Retask of a done slug preserves the original created_at; new slug → now.
        created_at = existing["created_at"] if existing is not None else ts
        await store.persist_created_task(
            task_id=slug,
            description=description,
            status=_RUNNING,
            created_at=created_at,
            updated_at=ts,
            worktrees=worktrees,
        )

        return {
            "task_id": slug,
            "status": _RUNNING,
            "worktrees": [
                {"repo_path": repo, "worktree_path": wt_path, "branch": br}
                for repo, br, wt_path in worktrees
            ],
        }
    finally:
        for lock in reversed(acquired):  # release in reverse order
            lock.release()
