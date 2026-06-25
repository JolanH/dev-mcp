"""Story 2.1 — pure, task-grouped derive-on-read projection (AC 1-4).

Fully synchronous, pure unit tests: NO git, NO Store, NO ``run_git`` and NO
``tmp_git_repo``. Inputs are hand-built ``WorktreeEntry`` objects + ``list_tasks``
shaped dicts — ``project()`` is pure and total by construction, so there is zero
git-safety surface here (project-context.md#Git safety in tests).

Coverage map:
* AC1 — grouping into one ``TaskView`` per slug, ``task_id``/``repo_path`` sort,
  ``generated_at`` passthrough, task-level fields copied from the row.
* AC2 — a link whose branch is absent from its repo porcelain → ``orphaned`` +
  exact ``orphan_link:…`` warning; never dropped, never status-mutated; a
  fully-orphaned task is still surfaced.
* AC3 — an unlinked ``agent/<slug>`` worktree → task-less ``TaskView``; a
  non-``agent/`` worktree (e.g. ``master``) is NOT surfaced (Decision A).
* AC4 — purity/totality: no throw on orphan/detached/empty/absent-repo; two
  identical calls produce equal snapshots; ``asdict()`` is all-snake_case.
"""

import dataclasses
import json

from dev_helper_mcp.git.porcelain import WorktreeEntry
from dev_helper_mcp.projection import (
    CacheSnapshot,
    TaskView,
    WorktreeView,
    project,
)

GEN = "2026-06-25T12:00:00Z"


def _entry(
    path: str,
    branch: str | None,
    *,
    detached: bool = False,
    locked: bool = False,
    prunable: bool = False,
    bare: bool = False,
) -> WorktreeEntry:
    """A porcelain entry with a recognisable HEAD derived from the path."""
    return WorktreeEntry(
        path=path,
        branch=branch,
        head=None if detached else "deadbeef",
        detached=detached,
        locked=locked,
        prunable=prunable,
        bare=bare,
    )


def _task(
    task_id: str, worktrees: list[dict], *, status: str = "active", description: str | None = "desc"
) -> dict:
    """A ``store.list_tasks()`` shaped row (task fields + nested links)."""
    return {
        "task_id": task_id,
        "description": description,
        "status": status,
        "created_at": "2026-06-24T09:00:00Z",
        "updated_at": "2026-06-24T10:00:00Z",
        "worktrees": worktrees,
    }


def _link(repo_path: str, slug: str, worktree_path: str) -> dict:
    return {
        "repo_path": repo_path,
        "branch": f"agent/{slug}",
        "worktree_path": worktree_path,
    }


# ── AC1: grouping + ordering + passthrough ──


def test_two_repo_task_groups_into_one_taskview_sorted_by_repo():
    git_listings = {
        "/code/beta": [_entry("/code/beta.worktrees/feat", "agent/feat")],
        "/code/alpha": [_entry("/code/alpha.worktrees/feat", "agent/feat")],
    }
    tasks = [
        _task(
            "feat",
            [
                _link("/code/beta", "feat", "/code/beta.worktrees/feat"),
                _link("/code/alpha", "feat", "/code/alpha.worktrees/feat"),
            ],
        )
    ]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert isinstance(snap, CacheSnapshot)
    assert len(snap.tasks) == 1
    tv = snap.tasks[0]
    assert isinstance(tv, TaskView)
    assert tv.task_id == "feat"
    # worktrees sorted by repo_path ASC
    assert [w.repo_path for w in tv.worktrees] == ["/code/alpha", "/code/beta"]
    assert all(isinstance(w, WorktreeView) and not w.orphaned for w in tv.worktrees)


def test_tasks_sorted_by_task_id_and_generated_at_passthrough():
    git_listings = {
        "/code/repo": [
            _entry("/code/repo.worktrees/zeta", "agent/zeta"),
            _entry("/code/repo.worktrees/alpha", "agent/alpha"),
        ],
    }
    tasks = [
        _task("zeta", [_link("/code/repo", "zeta", "/code/repo.worktrees/zeta")]),
        _task("alpha", [_link("/code/repo", "alpha", "/code/repo.worktrees/alpha")]),
    ]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert [t.task_id for t in snap.tasks] == ["alpha", "zeta"]
    assert snap.generated_at == GEN


def test_task_level_fields_copied_from_row():
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/feat", "agent/feat")]}
    tasks = [
        _task(
            "feat",
            [_link("/code/repo", "feat", "/code/repo.worktrees/feat")],
            status="done",
            description="ship it",
        )
    ]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    tv = snap.tasks[0]
    assert tv.status == "done"
    assert tv.description == "ship it"
    assert tv.created_at == "2026-06-24T09:00:00Z"
    assert tv.updated_at == "2026-06-24T10:00:00Z"


def test_matched_worktree_fields_copied_from_porcelain():
    git_listings = {
        "/code/repo": [
            _entry("/code/repo.worktrees/feat", "agent/feat", locked=True, prunable=True)
        ]
    }
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    w = snap.tasks[0].worktrees[0]
    assert w.path == "/code/repo.worktrees/feat"
    assert w.head == "deadbeef"
    assert w.detached is False
    assert w.locked is True
    assert w.prunable is True
    assert w.orphaned is False
    # WorktreeView intentionally drops `bare`.
    assert not hasattr(w, "bare")


def test_done_task_is_surfaced_not_filtered():
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/feat", "agent/feat")]}
    tasks = [
        _task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")], status="done")
    ]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert [t.task_id for t in snap.tasks] == ["feat"]
    assert snap.tasks[0].status == "done"


# ── AC2: orphan link detection ──


def test_link_branch_absent_from_porcelain_is_orphaned_with_warning():
    git_listings = {"/code/repo": []}  # repo present, branch gone
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    tv = snap.tasks[0]
    assert len(tv.worktrees) == 1
    w = tv.worktrees[0]
    assert w.orphaned is True
    assert w.path is None
    assert w.head is None
    assert w.detached is False
    assert w.locked is False
    assert w.prunable is False
    assert w.repo_path == "/code/repo"
    assert w.branch == "agent/feat"
    assert snap.warnings == ("orphan_link:feat@/code/repo:agent/feat",)


def test_link_in_absent_repo_is_orphaned():
    git_listings = {}  # repo not fanned out / deleted
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    w = snap.tasks[0].worktrees[0]
    assert w.orphaned is True
    assert snap.warnings == ("orphan_link:feat@/code/repo:agent/feat",)


def test_fully_orphaned_task_is_surfaced_not_dropped():
    git_listings = {"/code/a": [], "/code/b": []}
    tasks = [
        _task(
            "feat",
            [
                _link("/code/a", "feat", "/code/a.worktrees/feat"),
                _link("/code/b", "feat", "/code/b.worktrees/feat"),
            ],
        )
    ]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert len(snap.tasks) == 1
    tv = snap.tasks[0]
    assert tv.task_id == "feat"
    assert all(w.orphaned for w in tv.worktrees)
    assert snap.warnings == (
        "orphan_link:feat@/code/a:agent/feat",
        "orphan_link:feat@/code/b:agent/feat",
    )


def test_partially_orphaned_task_keeps_both_cells():
    git_listings = {
        "/code/a": [_entry("/code/a.worktrees/feat", "agent/feat")],
        "/code/b": [],
    }
    tasks = [
        _task(
            "feat",
            [
                _link("/code/a", "feat", "/code/a.worktrees/feat"),
                _link("/code/b", "feat", "/code/b.worktrees/feat"),
            ],
        )
    ]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    tv = snap.tasks[0]
    by_repo = {w.repo_path: w for w in tv.worktrees}
    assert by_repo["/code/a"].orphaned is False
    assert by_repo["/code/b"].orphaned is True
    assert snap.warnings == ("orphan_link:feat@/code/b:agent/feat",)


# ── AC3: untracked agent worktrees (Decision A) ──


def test_unlinked_agent_worktree_is_surfaced_as_taskless_view():
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/crash", "agent/crash")]}
    tasks: list[dict] = []  # crash-orphan: worktree exists, no DB row

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert len(snap.tasks) == 1
    tv = snap.tasks[0]
    assert tv.task_id == "crash"
    assert tv.status is None
    assert tv.description is None
    assert tv.created_at is None
    assert tv.updated_at is None
    assert len(tv.worktrees) == 1
    w = tv.worktrees[0]
    assert w.orphaned is False
    assert w.branch == "agent/crash"
    assert w.path == "/code/repo.worktrees/crash"


def test_non_agent_worktree_is_not_surfaced():
    git_listings = {
        "/code/repo": [
            _entry("/code/repo", "master"),  # the repo's own main checkout
            _entry("/code/repo.worktrees/feat", "agent/feat"),
        ]
    }
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert [t.task_id for t in snap.tasks] == ["feat"]
    # `master` must not produce a phantom card.
    assert all(w.branch != "master" for t in snap.tasks for w in t.worktrees)


def test_unlinked_agent_worktree_groups_with_linked_cells_of_same_slug():
    # repo A has a real link; repo B has an unlinked agent worktree of same slug.
    git_listings = {
        "/code/a": [_entry("/code/a.worktrees/feat", "agent/feat")],
        "/code/b": [_entry("/code/b.worktrees/feat", "agent/feat")],
    }
    tasks = [_task("feat", [_link("/code/a", "feat", "/code/a.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert len(snap.tasks) == 1
    tv = snap.tasks[0]
    assert tv.status == "active"  # task-level fields from the DB row
    assert [w.repo_path for w in tv.worktrees] == ["/code/a", "/code/b"]
    assert all(not w.orphaned for w in tv.worktrees)


def test_linked_agent_worktree_not_double_emitted_as_untracked():
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/feat", "agent/feat")]}
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    assert len(snap.tasks[0].worktrees) == 1  # the link, not link + untracked dupe


def test_bare_agent_branch_does_not_emit_phantom_empty_slug_task():
    # An entry whose branch is exactly the prefix (stripped slug == "") must not
    # become a TaskView(task_id="") — git can't produce it, but the pure function
    # is total over its typed inputs (AC4).
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/x", "agent/")]}

    snap = project(git_listings=git_listings, tasks=[], generated_at=GEN)

    assert snap.tasks == ()
    assert all(t.task_id != "" for t in snap.tasks)


def test_duplicate_unclaimed_agent_entry_not_double_emitted():
    # Two unclaimed porcelain entries on the same (repo, agent/branch) must emit
    # ONE untracked cell, not two (AC4 totality / determinism over typed input).
    git_listings = {
        "/code/repo": [
            _entry("/code/repo.worktrees/crash", "agent/crash"),
            _entry("/code/repo.worktrees/crash-dup", "agent/crash"),
        ]
    }

    snap = project(git_listings=git_listings, tasks=[], generated_at=GEN)

    assert len(snap.tasks) == 1
    assert len(snap.tasks[0].worktrees) == 1


# ── AC4: purity / totality / determinism ──


def test_empty_inputs_return_empty_snapshot():
    snap = project(git_listings={}, tasks=[], generated_at=GEN)
    assert snap == CacheSnapshot(generated_at=GEN, tasks=(), warnings=())


def test_task_with_zero_links_has_empty_worktrees():
    snap = project(git_listings={}, tasks=[_task("feat", [])], generated_at=GEN)
    assert len(snap.tasks) == 1
    assert snap.tasks[0].worktrees == ()
    assert snap.warnings == ()


def test_detached_head_entry_does_not_crash_and_link_reads_orphaned():
    # Detached agent worktree parses to branch=None → not in present index →
    # the stored link reads orphaned (known deferred false-positive, not a 2.1 bug).
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/feat", None, detached=True)]}
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)

    w = snap.tasks[0].worktrees[0]
    assert w.orphaned is True
    # The detached entry has no agent/ branch → not surfaced as untracked either.
    assert len(snap.tasks) == 1


def test_projection_is_independent_of_input_insertion_order():
    # Two inputs with IDENTICAL contents but DIFFERENT insertion orders must
    # produce equal snapshots — the real determinism property (the output sorts
    # tasks/worktrees/warnings, so it must not depend on dict/list ordering).
    listings_a = {
        "/code/b": [_entry("/code/b.worktrees/feat", "agent/feat")],
        "/code/a": [
            _entry("/code/a.worktrees/feat", "agent/feat"),
            _entry("/code/a.worktrees/orphan-x", "agent/loose"),
        ],
    }
    tasks_a = [
        _task("zeta", [_link("/code/a", "zeta", "/code/a.worktrees/zeta")]),  # orphaned
        _task(
            "feat",
            [
                _link("/code/a", "feat", "/code/a.worktrees/feat"),
                _link("/code/b", "feat", "/code/b.worktrees/feat"),
            ],
        ),
    ]
    # Same contents, reversed repo order, reversed entry order, reversed task order.
    listings_b = {
        "/code/a": [
            _entry("/code/a.worktrees/orphan-x", "agent/loose"),
            _entry("/code/a.worktrees/feat", "agent/feat"),
        ],
        "/code/b": [_entry("/code/b.worktrees/feat", "agent/feat")],
    }
    tasks_b = [
        _task(
            "feat",
            [
                _link("/code/b", "feat", "/code/b.worktrees/feat"),
                _link("/code/a", "feat", "/code/a.worktrees/feat"),
            ],
        ),
        _task("zeta", [_link("/code/a", "zeta", "/code/a.worktrees/zeta")]),
    ]

    snap_a = project(git_listings=listings_a, tasks=tasks_a, generated_at=GEN)
    snap_b = project(git_listings=listings_b, tasks=tasks_b, generated_at=GEN)
    assert snap_a == snap_b
    # A repeated call on the same input is also stable.
    assert project(git_listings=listings_a, tasks=tasks_a, generated_at=GEN) == snap_a
    assert list(snap_a.warnings) == sorted(snap_a.warnings)


def test_asdict_is_all_snake_case_with_pinned_keys():
    git_listings = {"/code/repo": [_entry("/code/repo.worktrees/feat", "agent/feat")]}
    tasks = [_task("feat", [_link("/code/repo", "feat", "/code/repo.worktrees/feat")])]

    snap = project(git_listings=git_listings, tasks=tasks, generated_at=GEN)
    d = dataclasses.asdict(snap)

    # The dict must be JSON-serialisable as-is (the Story 2.3 `/state` payload,
    # no translation layer) — nested frozen dataclasses + tuples round-trip.
    assert json.loads(json.dumps(d)) == json.loads(json.dumps(dataclasses.asdict(snap)))

    assert set(d) == {"generated_at", "tasks", "warnings"}
    tv = d["tasks"][0]
    assert set(tv) == {
        "task_id",
        "description",
        "status",
        "created_at",
        "updated_at",
        "worktrees",
    }
    wv = tv["worktrees"][0]
    assert set(wv) == {
        "repo_path",
        "branch",
        "path",
        "head",
        "detached",
        "locked",
        "prunable",
        "orphaned",
    }
