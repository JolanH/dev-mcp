# Story 2.1: Derive-on-read projection (pure, task-grouped)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer building the tool,
I want a pure function that joins live per-repo git worktree listings with the Store's task records into a task-grouped view,
so that the dashboard view is always a faithful projection of git with no stored derived state to drift.

## Acceptance Criteria

1. **Task-grouped snapshot, sorted (FR-12 view).**
   **Given** per-repo `git worktree list --porcelain` outputs and the Store's `task` + `task_worktree` rows,
   **When** the projection runs,
   **Then** it returns a `CacheSnapshot` grouped by task (`TaskView` → per-repo `WorktreeView`) with `generated_at`, tasks sorted by `task_id` ASC and worktrees by `repo_path` ASC.

2. **Orphaned link surfaced, never auto-deleted/auto-done (Orphaned-link rule).**
   **Given** a `task_worktree` link whose `branch` is absent from its repo's porcelain,
   **When** the projection runs,
   **Then** that worktree is emitted with `orphaned: true` AND surfaced in `warnings` as `orphan_link:<task_id>@<repo>:<branch>`; it is never auto-deleted and never auto-`done`.

3. **Untracked worktree surfaced, not dropped.**
   **Given** a worktree present in a repo's git with no matching link,
   **When** the projection runs,
   **Then** it is surfaced as a task-less/untracked entry, not dropped.

4. **Purity + totality.**
   **Given** the projection function,
   **When** it executes,
   **Then** it performs no writes, no git/DB I/O, and no destructive git op (purity test), and is total — it never throws on orphans or malformed-but-parsed input.

## Tasks / Subtasks

- [ ] **Task 1 — define the snapshot dataclasses (AC: 1, 2, 3)**
  - [ ] `CacheSnapshot{generated_at: str, tasks: list[TaskView], warnings: list[str]}`
  - [ ] `TaskView{task_id, description: str|None, status: str|None, created_at: str|None, updated_at: str|None, worktrees: list[WorktreeView]}`
  - [ ] `WorktreeView{repo_path, branch, path: str|None, head: str|None, detached: bool, locked: bool, prunable: bool, orphaned: bool}`
  - [ ] Immutable value objects (frozen dataclasses or equivalent) — the snapshot is swapped whole, never mutated in place
- [ ] **Task 2 — `core/projection.py`: the pure `derive()` (AC: 1, 2, 3, 4)**
  - [ ] Signature: `derive(git_listings: dict[repo_path, list[parsed_porcelain_entry]], task_rows, task_worktree_rows, generated_at: str) -> CacheSnapshot` — takes already-fetched data; performs NO git/DB I/O itself
  - [ ] **Join rule:** for each tracked repo, porcelain is the existence set; `task_worktree` links LEFT-JOIN on `(repo_path, branch)`
  - [ ] Worktree present in git with no link → emit under a synthetic task-less / `untracked` entry (do not drop)
  - [ ] Link whose branch is absent from its repo's porcelain → emit `orphaned: true` + append `orphan_link:<task_id>@<repo>:<branch>` to `warnings`
  - [ ] Group by task into `TaskView`s; sort tasks by `task_id` ASC, worktrees by `repo_path` ASC; `warnings == []` when clean
  - [ ] Total: never throws on orphaned/malformed-but-parsed input (defensive over missing optional fields)
- [ ] **Task 3 — tests (`test_projection.py`; under AR-12 gate)**
  - [ ] Purity: a spy/fake asserts the function performs no writes and no git/DB calls; calling it twice on the same input yields equal snapshots (idempotent)
  - [ ] Orphan detection: a link with a branch absent from porcelain → `orphaned:true` + the exact `warnings` string
  - [ ] Untracked: a porcelain worktree with no link → task-less entry present
  - [ ] Multi-repo grouping: a task spanning 3 repos → one `TaskView` with 3 sorted `WorktreeView`s; multiple tasks sorted by `task_id`
  - [ ] Totality: malformed-but-parsed / empty inputs do not raise

## Dev Notes

### Scope boundaries — read first
This is the **pure data join only** — the first Epic 2 story. **Explicitly OUT of scope (do not pull forward):**
- **No I/O** — `derive()` takes already-fetched git listings + DB rows and returns a snapshot. It does NOT call `run_git()`, does NOT open the Store, does NOT touch the network. Fetching is **Story 2.2** (the cache/refresher).
- **No cache object / background tick / `/state`** — Story 2.2 / 2.3.
- **No HTML/CSS/JS** — Stories 2.4a–c.
- **No destructive git op, ever** — orphans are surfaced, never auto-deleted or auto-`done`.

### This is the keystone of the consistency model (architecture.md § Invariants)
- **Invariant 4 — derive-on-read; never persist derived state.** This function IS the derive-on-read projection. It makes permanent git↔DB drift structurally impossible: git porcelain is the sole existence truth, the DB holds only task records, and the view is recomputed, never stored. [Source: architecture.md#Invariants; #Derived State & Refresh Model]
- **Derive-on-read purity (cross-cutting):** the projection is a pure function of `(git_listing, annotations)` with NO write-back during a read; must be total (never throws on orphans). [Source: architecture.md#Cross-Cutting Concerns; #Structure & Process Patterns]

### Exact snapshot shape (copy precisely — architecture.md § Derived State & Refresh Model)
```
CacheSnapshot: generated_at:str · tasks:list[TaskView] (sorted task_id ASC) · warnings:list[str]
TaskView:      task_id:str · description:str|None · status:str|None · created_at:str|None · updated_at:str|None · worktrees:list[WorktreeView] (sorted repo_path ASC)
WorktreeView:  repo_path:str · branch:str · path:str|None · head:str|None · detached:bool · locked:bool · prunable:bool · orphaned:bool
```
`description`/`status` are `None` only for a task-less (untracked) entry; `path` is `None` for a link-only orphan. The snapshot is **immutable and swapped whole**. This is the ONLY shape `/state` (2.3) and the refresh tick (2.2) share — do not invent a parallel shape. [Source: architecture.md#Derived State & Refresh Model]

### Orphaned-link rule (the core consistency rule — architecture.md § Data Architecture)
A `task_worktree` link whose branch is absent from its repo's `git worktree list` is **shown, flagged orphaned, NEVER auto-deleted and never auto-`done`** — a git op must never silently eat the non-derivable task records we store. A task all of whose links are orphaned is surfaced as a fully-orphaned task, not dropped. [Source: architecture.md#Data Architecture; #Orphaned-link rule]

### Builds on Stories 1.2 + 1.6 (previous-story intelligence)
- Consumes the output of `git/porcelain.py` (1.2) — the parsed porcelain entries (path, head, branch, detached, locked, prunable) are the `git_listings` input. Match those field names.
- Consumes `task` + `task_worktree` row shapes from `store.py` (1.2) and the four-state status from 1.6.
- `core/projection.py` is **core layer — imports nothing from `mcp`/`starlette`** (keep `test_adapter_seam.py` green). It is pure (no I/O) — it must not import `store.py` or `git/runner.py` either; callers pass data in.
- Use `now_iso()` for `generated_at` only if the caller doesn't supply it; prefer taking `generated_at` as an argument so the function stays pure/deterministic for tests.

### Source tree components to touch
`core/projection.py` (new; the dataclasses can live here or in a small `core` types module); `test_projection.py`; `tests/fixtures/porcelain/` reused from 1.2. [Source: architecture.md#Complete Project Directory Structure; #Data boundary — projection.py consumes outputs and is pure (no I/O)]

### Project Structure Notes
- `projection.py` is the pure consumer; `cache.py` (2.2) is the ONLY writer of the in-memory view. Keep the write/refresh logic OUT of this story. [Source: architecture.md#Architectural Boundaries]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 2.1: Derive-on-read projection (pure, task-grouped)] — acceptance criteria
- [Source: epics.md#FR-12] derive-on-read view
- [Source: architecture.md#Derived State & Refresh Model] — exact CacheSnapshot/TaskView/WorktreeView shape + join rule
- [Source: architecture.md#Data Architecture] — orphaned-link rule
- [Source: architecture.md#Invariants] — invariant 4; [Source: architecture.md#Architectural Boundaries] — purity boundary

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
