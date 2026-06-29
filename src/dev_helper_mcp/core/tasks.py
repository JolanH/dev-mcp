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

from ..config import (
    TASK_STATUSES,
    branch_name_for,
    legal_transition,
    worktree_path_for,
)
from ..core.mutator import GitRepoMutator, RepoMutator
from ..core.slug import slugify
from ..errors import (
    ActiveTaskConflict,
    BaseRefNotFound,
    BranchExists,
    DevHelperError,
    InvalidStatus,
    InvalidTaskName,
    NoDefaultBaseRef,
    NoDefaultRepo,
    RollbackIncomplete,
    TaskNotFound,
    WorktreePathInUse,
)
from ..git.repo_lock import RepoLockRegistry
from ..git.runner import GitRunner, Pool
from ..store import Store
from ..util import now_iso

_RUNNING = "running"


async def resolve_default_repo(*, runner: GitRunner, cwd: str | None = None) -> str:
    """Resolve the repo ``create_task`` defaults ``repos`` to when it is omitted: the
    git work tree containing the server's current directory (where the CLI was launched).

    Returns the work tree's toplevel path. Raises :class:`NoDefaultRepo` when the cwd is
    not inside a git work tree — the caller must then pass ``repos`` explicitly. The git
    call goes through the injected ``runner`` (Invariant 1); ``cwd`` is injectable so the
    resolution is unit-testable without ``os.chdir``.
    """
    base = os.getcwd() if cwd is None else cwd
    result = await runner.run_git(base, ["rev-parse", "--show-toplevel"], pool=Pool.READ)
    if result.returncode != 0:
        raise NoDefaultRepo(
            "current directory is not inside a git repository; pass `repos` explicitly",
            {"cwd": base},
        )
    return result.stdout.decode(errors="replace").strip()


async def resolve_default_base_ref(*, runner: GitRunner, cwd: str | None = None) -> str:
    """Resolve the ref ``create_task`` defaults ``base_ref`` to when it is omitted: the
    branch currently checked out in the server's current directory.

    Returns the short branch name. Raises :class:`NoDefaultBaseRef` when the cwd is not
    on a branch — a detached HEAD or a non-repo (``git symbolic-ref`` exits non-zero in
    both cases) — so the caller must pass ``base_ref`` explicitly. Goes through the
    injected ``runner`` (Invariant 1); ``cwd`` is injectable for testing.
    """
    base = os.getcwd() if cwd is None else cwd
    result = await runner.run_git(
        base, ["symbolic-ref", "--quiet", "--short", "HEAD"], pool=Pool.READ
    )
    branch = result.stdout.decode(errors="replace").strip()
    if result.returncode != 0 or not branch:
        raise NoDefaultBaseRef(
            "current directory is not on a git branch (detached HEAD or not a repo); "
            "pass `base_ref` explicitly",
            {"cwd": base},
        )
    return branch


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


async def update_task(
    task_id: str,
    *,
    status: str | None = None,
    description: str | None = None,
    store: Store,
) -> dict:
    """Update an EXISTING task's status and/or description; bump ``updated_at`` (Story 1.6).

    SDK-free core: plain args, an injected ``store``, raises a typed ``DevHelperError``.
    UPDATE-only — never UPSERT, never create. Order of operations:

    1. Resolve the task (``get_task``); ``None`` → ``TaskNotFound`` (AC 3).
    2. Validate ``status`` (when given): out of :data:`TASK_STATUSES` →
       ``InvalidStatus(reason="not_in_set")`` (AC 1).
    3. Validate the transition: ``not legal_transition(current, status)`` →
       ``InvalidStatus(reason="illegal_transition")`` (AC 2). ``done`` is terminal, so a
       ``done`` task rejects ANY status update; active→{active,done} all pass.
    4. Apply via ``store.update_task`` (``created_at`` preserved, ``updated_at`` bumped).

    Takes NO per-repo mutex (Invariant 12 / AR-14: only per-repo git mutations
    serialize; this touches the ``task`` row, not a worktree). AC 4 ("done releases the
    slug, flags closed") is a consequence, not extra code: ``status='done'`` makes the
    task non-active because "active" is defined everywhere as ``status != 'done'`` (the
    ``create`` gate above) — the row and its links are kept (the dashboard folds it; a
    later ``create_task`` of the same slug succeeds). Returns the updated task dict.
    """
    existing = await store.get_task(task_id)
    if existing is None:
        raise TaskNotFound("no such task", {"task_id": task_id})

    # No-op guard: nothing to change → return the task unchanged with NO DB write and
    # NO updated_at bump (a meaningless timestamp mutation Epic 2's staleness/sort could
    # key on). A no-op against a missing task still raises TaskNotFound (above).
    if status is None and description is None:
        return {
            "task_id": task_id,
            "status": existing["status"],
            "description": existing["description"],
            "created_at": existing["created_at"],
            "updated_at": existing["updated_at"],
        }

    if status is not None:
        if status not in TASK_STATUSES:
            raise InvalidStatus(
                "status is not one of the four legal states",
                {
                    "task_id": task_id,
                    "status": status,
                    "allowed": list(TASK_STATUSES),
                    "reason": "not_in_set",
                },
            )
        if not legal_transition(existing["status"], status):
            raise InvalidStatus(
                "illegal status transition (done is terminal)",
                {
                    "task_id": task_id,
                    "from": existing["status"],
                    "to": status,
                    "reason": "illegal_transition",
                },
            )

    ts = now_iso()
    matched = await store.update_task(
        task_id, status=status, description=description, updated_at=ts
    )
    if not matched:
        # Safety net: the row vanished between the get_task precheck and this write
        # (e.g. a concurrent remove_worktree last-worktree delete) — surface it rather
        # than fabricate success from the now-stale `existing` snapshot.
        raise TaskNotFound("no such task", {"task_id": task_id})
    return {
        "task_id": task_id,
        "status": status if status is not None else existing["status"],
        "description": description if description is not None else existing["description"],
        "created_at": existing["created_at"],
        "updated_at": ts,
    }


async def list_tasks(
    *,
    status: str | None = None,
    repo: str | None = None,
    store: Store,
) -> list[dict]:
    """List tasks (filterable) with all model fields + per-repo links (AC 5).

    A thin core wrapper over ``store.list_tasks`` — a Store read, NOT a live-git
    fan-out and NOT a cache (that view is Epic 2). Takes no mutex (read path). Empty /
    falsy ``status``/``repo`` mean "no filter" (the 1.5 fix — an empty-string ``repo``
    must NOT ``abspath("")`` to the cwd); a non-empty ``repo`` is abspath'd to match the
    absolute ``repo_path`` stored on links. Raises only what the store surfaces.
    """
    repo_filter = os.path.abspath(repo) if repo else None
    status_filter = status if status else None
    return await store.list_tasks(status=status_filter, repo=repo_filter)
