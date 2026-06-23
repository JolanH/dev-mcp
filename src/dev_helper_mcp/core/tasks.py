"""``create_task`` orchestrator (core layer — no ``mcp``/``starlette`` import).

Builds a multi-repo task: one isolated worktree + ``agent/<slug>`` branch per
repo, all-or-nothing. Dependencies (``GitRunner``, ``RepoLockRegistry``, ``Store``)
are *injected* — core never constructs the runner/store (testability + the
"asyncio objects live in the running loop" rule). Returns the plain success
``data`` dict; raises a typed :class:`DevHelperError` on any guard (the adapter
converts it to the ``{ok, data, error}`` envelope).

Scope (Story 1.4): happy path + preflight rejection (1.3) **plus** post-preflight
cross-repo rollback. A preflight error is still raised before any mutation (cheapest
rollback — nothing to undo). If a git mutation fails *after* preflight passed, every
already-created worktree is torn down in **reverse creation order** via the injected
:class:`RepoMutator`; a clean rollback re-raises the original cause, while a
compensation that itself fails escalates to ``RollbackIncomplete`` (orphans named,
original cause preserved). Crash-safety (SIGKILL mid-call) is an explicit v1 non-goal.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import branch_name_for, worktree_path_for
from ..core.mutator import GitRepoMutator, RepoMutator
from ..core.slug import slugify
from ..errors import (
    ActiveTaskConflict,
    BaseRefNotFound,
    BranchExists,
    DevHelperError,
    InvalidTaskName,
    RollbackIncomplete,
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
    mutator: RepoMutator | None = None,
) -> dict:
    """Create the task across every repo in ``repos``; return the success ``data``.

    Raises ``InvalidTaskName``/``NotAGitRepo``/``BranchExists``/``WorktreePathInUse``
    /``BaseRefNotFound``/``ActiveTaskConflict`` in preflight (nothing mutated). If a
    git mutation fails after preflight, already-created worktrees are torn down in
    reverse order and the original cause is re-raised — or ``RollbackIncomplete`` if a
    teardown itself fails (orphaned repos named, original cause preserved).

    ``mutator`` is the injectable create/teardown seam (for deterministic fault
    injection in tests); when ``None`` it defaults to ``GitRepoMutator(runner)`` so
    production callers and existing tests need no change.
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

        # ── Provisioning + reverse-order compensation (AC 1, 3) — preflight passed. ──
        # Default the seam to the production mutator (keeps the per-loop "build inside
        # the running loop" rule — runner is already loop-bound).
        if mutator is None:
            mutator = GitRepoMutator(runner)
        start_point = base_ref or "HEAD"
        provisioned: list[tuple[str, str, str]] = []
        try:
            for repo, wt_path in plan:
                await mutator.add(repo, branch, str(wt_path), start_point)
                # Append ONLY after `add` returns: the failing repo must NOT be in
                # `provisioned`, or compensation would try to tear down a worktree
                # that was never created (spurious teardown failure).
                provisioned.append((repo, branch, str(wt_path)))

            # Past the loop ⇒ every `add` succeeded; `provisioned` is the full set.
            worktrees = provisioned

            # ── Persist last, in one transaction (AC 2). ──
            # Kept INSIDE the try on purpose: a persist failure (a TOCTOU
            # ``ActiveTaskConflict`` slipping past the preflight gate, or a disk-full /
            # db-locked ``Internal``) must trigger the SAME reverse-order compensation.
            # Until the rows commit, the worktrees we just created are uncommitted
            # residue — leaving them on a persist failure would orphan worktrees +
            # branches with no DB row, defeating the all-or-nothing guarantee (AR-13).
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
        except DevHelperError as cause:
            # Tear down in reverse creation order ([A,B,C] failing at C → B then A).
            # Triggered by either a post-preflight ``add`` failure (the failing repo is
            # NOT in ``provisioned``) or a persist failure (``provisioned`` is the full
            # set, all worktrees torn down). Locks are still held (this is inside the
            # outer try; ``finally`` releases them only after rollback) so no concurrent
            # create sees a half-torn repo.
            orphaned_repos: list[str] = []
            compensation_errors: list[dict] = []
            for repo, br, wt in reversed(provisioned):
                try:
                    await mutator.remove(repo, br, wt)
                except DevHelperError as comp_exc:
                    orphaned_repos.append(repo)
                    compensation_errors.append({"repo": repo, "error": comp_exc.as_dict()})
            if orphaned_repos:
                # A compensation itself failed — escalate, but NEVER swallow `cause`.
                raise RollbackIncomplete(
                    "compensating teardown failed",
                    {
                        "orphaned_repos": orphaned_repos,
                        "original_cause": cause.as_dict(),
                        "compensation_errors": compensation_errors,
                    },
                ) from cause
            # Clean rollback: no rows written, no residue → re-raise the real cause.
            raise

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
