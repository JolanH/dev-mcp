"""Pure, task-grouped derive-on-read projection (Story 2.1, AC 1-4).

PURE derive-on-read: ``(git_listings, task_rows) → CacheSnapshot``. This module
is the *recompute* step of Invariant 4 — git porcelain (per tracked repo) is the
sole truth for worktree existence; the DB holds only task records + their
``(repo_path, branch, worktree_path)`` links; the view is never stored.

Guarantees, enforced structurally by the signature (it takes plain data, never a
``Store``/``GitRunner``):

* No git/DB I/O and no destructive op — it *cannot* do I/O (AC4, Invariants 1/10).
* No ``mcp``/``starlette`` import — pure core module, policed by
  ``tests/test_adapter_seam.py`` (Invariant 7).
* Total — never raises on orphan links, untracked entries, detached
  (``branch=None``) entries, empty inputs, or links into an absent repo (AC4).
* ``generated_at`` is **injected** by the caller (``now_iso()`` at the
  ``cache.py`` boundary, Story 2.2) — never read from the clock here, so the
  function is deterministic for the purity test (Decision B, Invariant 11).

Field names are the ``/state`` JSON contract (Invariant 3): all snake_case, so
``dataclasses.asdict(snapshot)`` is the payload Story 2.3 serialises with no
translation layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dev_helper_mcp.config import BRANCH_PREFIX
from dev_helper_mcp.git.porcelain import WorktreeEntry


@dataclass(frozen=True)
class WorktreeView:
    """One repo's cell of a task. The pinned 8-field shape (architecture.md:376-385).

    Drops ``WorktreeEntry.bare``; adds ``repo_path`` (the join key's repo side) and
    ``orphaned`` (a link with no live worktree). ``path``/``head`` are ``None`` and
    the boolean flags ``False`` when orphaned.
    """

    repo_path: str
    branch: str
    path: str | None
    head: str | None
    detached: bool
    locked: bool
    prunable: bool
    orphaned: bool


@dataclass(frozen=True)
class TaskView:
    """A task (slug), grouping its per-repo worktree cells (architecture.md:369-375).

    Task-level fields come from the DB ``task`` row; they are ``None`` for an
    untracked-only slug (a crash-orphan ``agent/<slug>`` worktree with no row).
    """

    task_id: str
    description: str | None
    status: str | None
    created_at: str | None
    updated_at: str | None
    worktrees: tuple[WorktreeView, ...]


@dataclass(frozen=True)
class CacheSnapshot:
    """Immutable, swapped-whole view (architecture.md:365-368)."""

    generated_at: str
    tasks: tuple[TaskView, ...]
    warnings: tuple[str, ...]


def project(
    *,
    git_listings: Mapping[str, list[WorktreeEntry]],
    tasks: list[dict],
    generated_at: str,
) -> CacheSnapshot:
    """Join per-repo git porcelain with Store task rows into a ``CacheSnapshot``.

    ``git_listings``: ``repo_path → parsed porcelain entries`` (the caller's
    per-repo fan-out, Story 2.2). ``tasks``: the ``store.list_tasks()`` shape —
    ``[{task_id, description, status, created_at, updated_at,
    worktrees:[{repo_path, branch, worktree_path}, …]}]``. ``generated_at``:
    injected ISO-8601 stamp (Decision B).

    The slug is the single join key. ``task_worktree`` links LEFT-JOIN on
    ``(repo_path, branch)`` against the porcelain "existence set"; unmatched links
    surface as orphaned + a warning; unclaimed ``agent/<slug>`` porcelain entries
    surface as untracked, task-less cells (Decision A). Pure and total — see the
    module docstring.
    """
    # Per repo, index the present (non-detached) branches → their porcelain entry.
    present: dict[str, dict[str, WorktreeEntry]] = {
        repo: {e.branch: e for e in entries if e.branch is not None}
        for repo, entries in git_listings.items()
    }

    # Accumulate worktree cells per slug, the task-level row per slug, and warnings.
    cells: dict[str, list[WorktreeView]] = {}
    rows: dict[str, dict] = {}
    warnings: list[str] = []
    # Track (repo_path, branch) claimed by a link so untracked emission skips them.
    claimed: set[tuple[str, str]] = set()

    # ── Tracked link cells (AC1, AC2) ──
    for task in tasks:
        task_id = task["task_id"]
        rows[task_id] = task
        cells.setdefault(task_id, [])
        for link in task["worktrees"]:
            repo_path = link["repo_path"]
            branch = link["branch"]
            claimed.add((repo_path, branch))
            entry = present.get(repo_path, {}).get(branch)
            if entry is not None:
                cells[task_id].append(
                    WorktreeView(
                        repo_path=repo_path,
                        branch=branch,
                        path=entry.path,
                        head=entry.head,
                        detached=entry.detached,
                        locked=entry.locked,
                        prunable=entry.prunable,
                        orphaned=False,
                    )
                )
            else:
                # Repo absent from the fan-out, OR branch gone from its porcelain.
                cells[task_id].append(
                    WorktreeView(
                        repo_path=repo_path,
                        branch=branch,
                        path=None,
                        head=None,
                        detached=False,
                        locked=False,
                        prunable=False,
                        orphaned=True,
                    )
                )
                warnings.append(f"orphan_link:{task_id}@{repo_path}:{branch}")

    # ── Untracked cells (AC3, Decision A: only the agent/ namespace) ──
    for repo_path, entries in git_listings.items():
        for entry in entries:
            branch = entry.branch
            if branch is None or not branch.startswith(BRANCH_PREFIX):
                continue
            # Skip a (repo, branch) already taken by a link OR already emitted as
            # untracked — a duplicate porcelain entry must not double-emit a cell.
            if (repo_path, branch) in claimed:
                continue
            slug = branch.removeprefix(BRANCH_PREFIX)
            if not slug:
                # A bare ``agent/`` branch carries no slug → no phantom task_id="".
                continue
            claimed.add((repo_path, branch))
            cells.setdefault(slug, [])
            cells[slug].append(
                WorktreeView(
                    repo_path=repo_path,
                    branch=branch,
                    path=entry.path,
                    head=entry.head,
                    detached=entry.detached,
                    locked=entry.locked,
                    prunable=entry.prunable,
                    orphaned=False,
                )
            )

    # ── Group into TaskViews (task-level fields from the row, else None) ──
    task_views: list[TaskView] = []
    for slug, views in cells.items():
        row = rows.get(slug, {})
        task_views.append(
            TaskView(
                task_id=slug,
                description=row.get("description"),
                status=row.get("status"),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
                # Sort by (repo_path, branch) for a TOTAL order — repo_path alone
                # leaves same-repo cells in accidental insertion order.
                worktrees=tuple(sorted(views, key=lambda w: (w.repo_path, w.branch))),
            )
        )

    return CacheSnapshot(
        generated_at=generated_at,
        tasks=tuple(sorted(task_views, key=lambda t: t.task_id)),
        warnings=tuple(sorted(warnings)),
    )
