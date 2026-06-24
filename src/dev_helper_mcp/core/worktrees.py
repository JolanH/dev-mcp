"""User-facing worktree list + guarded remove (core layer — no SDK import).

The architecture's named home for per-repo list/remove logic. SDK-free (imports
no ``mcp``/``starlette`` — policed by ``tests/test_adapter_seam.py``); takes plain
injected deps (``GitRunner``/``RepoLockRegistry``/``Store``); raises a typed
:class:`~dev_helper_mcp.errors.DevHelperError` the adapter converts to the envelope.

Two operations, two disciplines:

* :func:`list_worktrees` — a **read** (Invariant 4 derive-on-read): it fans
  ``git worktree list --porcelain`` per repo on the READ pool and LEFT-JOINs the
  stored links onto the live result. It takes NO mutex (Invariant 12: read/refresh
  ops do not serialize), does NO writes and NO destructive git, and **never**
  auto-deletes an orphaned link — it surfaces it flagged.
* :func:`remove_worktree` — a **mutation**: it holds the per-repo mutex across the
  whole critical section, runs destructive git on the MUTATION pool with the *safe*
  variants plus two distinct guards (``force`` for the dirty/locked worktree,
  ``force_unmerged_branch`` for an unmerged branch), and persists store changes only
  AFTER git succeeds. It is a SEPARATE code path from ``create_task``'s rollback
  teardown (``GitRepoMutator``, which force-tears-down a just-created clean worktree)
  — the two blast radii are deliberately not unified (deferred 1.4 review finding).
"""

from __future__ import annotations

import logging
import os

from ..errors import (
    DevHelperError,
    DirtyWorktree,
    Internal,
    LockedWorktree,
    TaskNotFound,
    UnmergedBranch,
)
from ..git.porcelain import parse_worktree_porcelain
from ..git.repo_lock import RepoLockRegistry
from ..git.runner import GitResult, GitRunner, Pool
from ..store import Store

logger = logging.getLogger(__name__)


async def list_worktrees(
    *,
    repo: str | None,
    task_id: str | None,
    runner: GitRunner,
    store: Store,
) -> list[dict]:
    """List every tracked worktree, joined live against git (AC 1).

    Reads the stored ``task_worktree`` links (filtered by ``repo``/``task_id``), then
    for each distinct repo fans out ``git worktree list --porcelain`` on the READ
    pool to learn which ``agent/<slug>`` branches still have a live worktree. Emits
    one entry per stored link — ``{repo_path, worktree_path, branch, task_id,
    status, orphaned}`` — where ``orphaned`` is ``True`` when the link's branch is
    absent from its repo's live porcelain (the worktree is gone but the row remains).
    Derive-on-read: orphaned links are shown+flagged, NEVER auto-deleted here.
    """
    # A falsy repo (None or "") means "no filter" — guard against the empty string,
    # which would otherwise abspath("") to the cwd and silently match the wrong repo.
    repo_filter = os.path.abspath(repo) if repo else None
    links = await store.list_worktree_links(repo=repo_filter, task_id=task_id)
    if not links:
        return []

    # Live derive (AC1: not a cache). One READ-pool porcelain per distinct repo;
    # collect the set of branches that currently have a live worktree there.
    live_branches: dict[str, set[str]] = {}
    for repo_path in sorted({link["repo_path"] for link in links}):
        try:
            result = await runner.run_git(
                repo_path, ["worktree", "list", "--porcelain"], pool=Pool.READ
            )
        except DevHelperError as exc:
            # run_git RAISES GitTimeout (hung/contended repo) and Internal (git
            # binary missing); a non-zero exit is RETURNED. Degrade the same way as a
            # non-zero rc so ONE bad repo cannot poison the whole list — never raise,
            # never delete. (Its links read orphaned for this call.)
            logger.warning(
                "worktree list raised for %s (%s); links will read orphaned",
                repo_path,
                exc.code,
            )
            live_branches[repo_path] = set()
            continue
        if result.returncode != 0:
            # Repo unreadable / vanished / not a git repo: treat as "no live
            # worktrees" so its links surface orphaned — never raise, never delete.
            logger.warning(
                "worktree list failed for %s (rc=%s); links will read orphaned",
                repo_path,
                result.returncode,
            )
            live_branches[repo_path] = set()
            continue
        entries = parse_worktree_porcelain(result.stdout)
        live_branches[repo_path] = {e.branch for e in entries if e.branch is not None}

    return [
        {
            "task_id": link["task_id"],
            "repo_path": link["repo_path"],
            "branch": link["branch"],
            "worktree_path": link["worktree_path"],
            "status": link["status"],
            "orphaned": link["branch"] not in live_branches.get(link["repo_path"], set()),
        }
        for link in links
    ]


# ── stderr classification (best-effort, English-substring; degrade to Internal) ──
# Same defensive approach as core.mutator._classify_add_failure. Locale-fragility
# caveat logged in deferred-work.md#story-1-4 applies: an unrecognized stderr
# degrades to Internal rather than mis-typing the guard.


def _is_dirty(stderr: str) -> bool:
    low = stderr.lower()
    return "contains modified or untracked files" in low or "use --force" in low


def _is_locked(stderr: str) -> bool:
    return "is locked" in stderr.lower()


def _is_unmerged(stderr: str) -> bool:
    return "not fully merged" in stderr.lower()


def _is_not_a_worktree(stderr: str) -> bool:
    # git: "fatal: '<path>' is not a working tree" — the worktree is already gone.
    return "is not a working tree" in stderr.lower()


def _is_branch_not_found(stderr: str) -> bool:
    # git: "error: branch '<name>' not found." — the branch is already gone.
    return "not found" in stderr.lower()


def _stderr(result: GitResult) -> str:
    return result.stderr.decode(errors="replace").strip()


async def remove_worktree(
    task_id: str,
    repo: str,
    *,
    delete_branch: bool = False,
    force: bool = False,
    force_unmerged_branch: bool = False,
    runner: GitRunner,
    locks: RepoLockRegistry,
    store: Store,
) -> dict:
    """Remove ONE worktree (the ``(task_id, repo)`` link), guarded (AC 2–5).

    Holds the per-repo mutex across the whole critical section. Resolves the target
    link (``TaskNotFound`` if absent), runs the *safe* ``git worktree remove`` — on a
    dirty/locked worktree it raises ``DirtyWorktree``/``LockedWorktree`` and changes
    NOTHING unless ``force=true``. With ``delete_branch=true`` it then runs the safe
    ``git branch -d``; an unmerged branch raises ``UnmergedBranch`` with the unmerged
    commit count surfaced FIRST in ``details`` (unless ``force_unmerged_branch=true``,
    which force-deletes via ``-D``). The two flags are distinct blast radii — one
    never silently authorizes the other.

    Persistence is LAST (after git succeeds): the link row is dropped, and per AC5 if
    it was the task's last worktree the ``task`` row is deleted (cascade clears the
    final link). Sibling repos' worktrees + rows are untouched (AC2). Returns the
    plain success ``data`` dict.
    """
    repo_abs = os.path.abspath(repo)
    lock = locks.lock_for(repo_abs)
    await lock.acquire()
    try:
        # Resolve the target link by (task_id, repo). Unknown slug/link → TaskNotFound.
        links = await store.list_worktree_links(repo=repo_abs, task_id=task_id)
        if not links:
            raise TaskNotFound(
                "no worktree for this task in this repo",
                {"task_id": task_id, "repo": repo_abs},
            )
        link = links[0]
        worktree_path = link["worktree_path"]
        branch = link["branch"]

        # ── Worktree removal + DirtyWorktree/LockedWorktree guard (AC3). ──
        rm_args = ["worktree", "remove"]
        if force:
            rm_args.append("--force")  # bypasses BOTH dirty and locked
        rm_args.append(worktree_path)
        rm = await runner.run_git(repo_abs, rm_args, pool=Pool.MUTATION)
        worktree_already_gone = False
        if rm.returncode != 0:
            text = _stderr(rm)
            if _is_not_a_worktree(text):
                # Idempotent: the worktree is already gone. Two cases reach here — the
                # documented UnmergedBranch retry (worktree removed, branch step left
                # the link intact), and a remove against an out-of-band-orphaned link.
                # Fall through to branch deletion + persistence rather than failing,
                # but record it so the caller can tell an orphan cleanup from a real
                # live-worktree removal (returned as `worktree_already_gone`).
                worktree_already_gone = True
                logger.info("worktree %s already removed; continuing", worktree_path)
            elif not force and _is_locked(text):
                # Nothing changed: no rows dropped, branch untouched.
                raise LockedWorktree(
                    "worktree is locked; pass force=true to override",
                    {
                        "task_id": task_id,
                        "repo": repo_abs,
                        "worktree_path": worktree_path,
                        "stderr": text,
                    },
                )
            elif not force and _is_dirty(text):
                raise DirtyWorktree(
                    "worktree has uncommitted changes; pass force=true to override",
                    {
                        "task_id": task_id,
                        "repo": repo_abs,
                        "worktree_path": worktree_path,
                        "stderr": text,
                    },
                )
            else:
                raise Internal(
                    "git worktree remove failed",
                    {
                        "task_id": task_id,
                        "repo": repo_abs,
                        "worktree_path": worktree_path,
                        "stderr": text,
                    },
                )

        # ── Branch deletion + UnmergedBranch guard (AC4) — only if requested. ──
        # The worktree is already gone (git refuses to delete a checked-out branch),
        # so a guard here does NOT roll the worktree back — surfaced in the error.
        if delete_branch:
            del_args = ["branch", "-D" if force_unmerged_branch else "-d", branch]
            br = await runner.run_git(repo_abs, del_args, pool=Pool.MUTATION)
            if br.returncode != 0:
                text = _stderr(br)
                if _is_branch_not_found(text):
                    # Idempotent: the branch is already gone (deleted out-of-band, or
                    # by a prior partial call that removed the worktree then raised
                    # UnmergedBranch and was retried after a manual branch delete).
                    # The desired end state holds — fall through to persistence rather
                    # than wedging the link with a permanent Internal on every retry.
                    logger.info("branch %s already gone; continuing", branch)
                elif not force_unmerged_branch and _is_unmerged(text):
                    # Surface the unmerged-commit count FIRST (the "what would be
                    # lost" preview) — computed on the READ pool.
                    count = await _unmerged_commit_count(runner, repo_abs, branch)
                    raise UnmergedBranch(
                        "branch has unmerged commits; pass force_unmerged_branch=true "
                        "to delete it (the worktree was already removed)",
                        {
                            "task_id": task_id,
                            "repo": repo_abs,
                            "branch": branch,
                            "unmerged_commits": count,
                            "worktree_removed": True,
                        },
                    )
                else:
                    raise Internal(
                        "git branch delete failed",
                        {"task_id": task_id, "repo": repo_abs, "branch": branch, "stderr": text},
                    )

        # ── Persist LAST, inside the held mutex (mirror create's rows-last). ──
        await store.delete_worktree(task_id, repo_abs)
        task_closed = False
        if await store.count_worktrees(task_id) == 0:
            # AC5: last worktree gone → the task ceases to be tracked (the cascade
            # clears the final link). 1.5 never mutates status — Story 1.6 owns that.
            await store.delete_task(task_id)
            task_closed = True

        return {
            "task_id": task_id,
            "repo_path": repo_abs,
            "worktree_path": worktree_path,
            "branch": branch,
            "branch_deleted": delete_branch,
            "task_closed": task_closed,
            "worktree_already_gone": worktree_already_gone,
        }
    finally:
        lock.release()


async def _unmerged_commit_count(runner: GitRunner, repo: str, branch: str) -> int:
    """Count commits unique to ``branch`` — reachable from no OTHER ref — READ pool.

    This is the "what would be lost on ``branch -D``" preview. NOTE: the naive
    ``rev-list --count <branch> --not --all`` returns 0, because ``--all`` includes
    ``<branch>`` itself (verified, git 2.34). So we enumerate every ref EXCEPT the
    target branch and use that as the negative set; with no other ref the branch's
    whole history is unique (everything would be lost). The count is a best-effort
    *informational* preview only — the ``UnmergedBranch`` guard fires regardless — so a
    garbled/failed git result degrades to ``0`` rather than failing the guard report.

    The positive revision is passed as the FULL refname (``refs/heads/<branch>``), not
    the bare branch: a full refname can never be mistaken for a CLI option, and it
    disambiguates the branch from a same-named tag.
    """
    refs = await runner.run_git(repo, ["for-each-ref", "--format=%(refname)"], pool=Pool.READ)
    if refs.returncode != 0:
        return 0
    full_branch = f"refs/heads/{branch}"
    others = [
        r for r in refs.stdout.decode(errors="replace").splitlines() if r and r != full_branch
    ]
    result = await runner.run_git(
        repo, ["rev-list", "--count", full_branch, "--not", *others], pool=Pool.READ
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.decode(errors="replace").strip() or "0")
    except ValueError:
        return 0
