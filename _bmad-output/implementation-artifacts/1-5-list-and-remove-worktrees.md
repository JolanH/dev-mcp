# Story 1.5: List and remove worktrees

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent or the developer,
I want to list worktrees across all tracked repos and remove one when its work is done,
so that I can see every isolated checkout and clean it up safely without touching the task's other repos.

## Acceptance Criteria

1. **`list_worktrees` — derived from per-repo git, not a cache (FR-2).**
   **Given** tasks with worktrees across several repos,
   **When** I call `list_worktrees(repo?, task_id?)`,
   **Then** it returns each worktree's `repo_path`, `worktree_path`, `branch`, and linked `task_id`/`status`, filtered as requested, derived from per-repo `git worktree list --porcelain -z` (not a stale cache).

2. **`remove_worktree` — single worktree, others unaffected (FR-3).**
   **Given** a worktree identified by `task_id`+`repo` (or by path),
   **When** I call `remove_worktree`,
   **Then** that repo's working tree is removed and de-tracked and its `task_worktree` row dropped;
   **And** the task's worktrees in other repos are unaffected.

3. **Dirty-worktree guard (FR-3, AR-8).**
   **Given** a worktree with uncommitted changes,
   **When** `remove_worktree` is called without `force`,
   **Then** `DirtyWorktree` is returned and nothing changes;
   **And** with `force=true` it is removed.

4. **Unmerged-branch guard — distinct flag, surface count first (FR-3, AR-8).**
   **Given** `delete_branch=true` for a branch with unmerged commits,
   **When** `remove_worktree` is called without `force_unmerged_branch`,
   **Then** `UnmergedBranch` is returned, surfacing the unmerged-commit count first;
   **And** with `force_unmerged_branch=true` the branch is deleted.

5. **Last-worktree-removed → task closed/detached.**
   **Given** a task whose last remaining worktree is removed,
   **When** the removal completes,
   **Then** the task record is marked closed/detached.

## Tasks / Subtasks

- [ ] **Task 1 — `tools/models.py`: `ListWorktreesIn` + `RemoveWorktreeIn` (AC: 1, 2, 3, 4)**
  - [ ] `ListWorktreesIn{repo: str | None, task_id: str | None}`
  - [ ] `RemoveWorktreeIn{task_id: str | None, repo: str | None, path: str | None, force: bool = False, delete_branch: bool = False, force_unmerged_branch: bool = False}` (identify by `task_id`+`repo` OR by `path`)
- [ ] **Task 2 — `core/worktrees.py`: `list()` (AC: 1)**
  - [ ] For each tracked `repo_path` (filtered if `repo` given), fan out `git worktree list --porcelain -z` via the **read pool**, parse with `git/porcelain.py` (1.2), LEFT-JOIN `task_worktree` links by `(repo_path, branch)` to attach `task_id`/`status`
  - [ ] Filter by `task_id` when supplied; return plain list of `{repo_path, worktree_path, branch, task_id, status}`
  - [ ] **Read path — never calls a destructive git op; never takes the per-repo mutex**
- [ ] **Task 3 — `core/worktrees.py`: `remove()` with two guards (AC: 2, 3, 4, 5)**
  - [ ] Resolve the target worktree by `task_id`+`repo` or by `path`; acquire the **per-repo mutex** for that repo
  - [ ] Dirty check (uncommitted changes) → without `force`: `DirtyWorktree`; with `force`: `git worktree remove --force`. Plain (clean) removal: `git worktree remove`
  - [ ] If `delete_branch`: detect unmerged commits and **surface the unmerged-commit count first**; without `force_unmerged_branch`: `UnmergedBranch`; with it: `git branch -D` (else `git branch -d`)
  - [ ] Drop the matching `task_worktree` row; the task's other-repo rows are untouched
  - [ ] If this was the task's **last** `task_worktree` row → mark the task closed/detached (define the closed/detached representation; do not delete the `task` row silently in a way that contradicts the schema — coordinate with the status model in 1.6)
- [ ] **Task 4 — `tools/handlers.py` + register both tools in `server_factory.py` (AC: 1, 2)**
  - [ ] `list_worktrees` + `remove_worktree` adapters: validate `*In` → call core → `{ok,data,error}`; map `DevHelperError` codes
- [ ] **Task 5 — tests (`test_worktrees.py`; under AR-12 gate; tmp git repos)**
  - [ ] `list_worktrees`: across several repos, filter by `repo` and by `task_id`, derived from porcelain (not cache); join attaches `task_id`/`status`
  - [ ] `remove_worktree`: removes + de-tracks + drops row; other-repo worktrees of the same task unaffected
  - [ ] Dirty guard: dirty worktree without `force` → `DirtyWorktree` (nothing changes); with `force` → removed
  - [ ] Unmerged guard: `delete_branch` on unmerged branch without `force_unmerged_branch` → `UnmergedBranch` with the commit count surfaced; with the flag → branch deleted
  - [ ] Last-worktree removal → task marked closed/detached
  - [ ] `test_tools.py` slice: both tools' envelope shape + snake_case keys

## Dev Notes

### Scope boundaries — read first
Adds the two worktree-lifecycle tools. **OUT of scope:** `update_task`/`list_tasks` and the full status transition matrix (Story 1.6); the in-memory cache, `/state`, dashboard (Epic 2). Note: `list_worktrees` reads **git directly** (per-repo fan-out at call time) — it is NOT the dashboard's cache read path (`/state` from cache is Epic 2). Two different read paths by design.

### Two distinct removal guards — distinct blast radii (architecture.md § Removal force-flag semantics)
- `force` → dirty/locked **worktree** removal (`git worktree remove --force`).
- `force_unmerged_branch` → **branch** deletion with unmerged commits (`git branch -D` vs `-d`), surfacing the unreachable/unmerged-commit count **first**.
These are two flags because they protect against two different losses; do not collapse them into one. The read/refresh path NEVER calls a destructive git op. [Source: architecture.md#Removal force-flag semantics; #Invariants invariant 10]

### Builds on Stories 1.2–1.4 (previous-story intelligence)
- From **1.2**: `git/porcelain.py` parser, `run_git()` (read pool for `list`, mutation pool for `remove`), `store.py` (drop `task_worktree` row; query links), the per-repo mutex, `errors.py` (`DirtyWorktree`, `UnmergedBranch`, `LockedWorktree`).
- From **1.3/1.4**: `core/worktrees.py` and `core/tasks.py` already exist; extend them (add `list`/`remove`) — do not duplicate logic. Reuse the `(repo_path, branch)` join convention.
- Adapter seam holds: `core/worktrees.py` imports no `mcp`/`starlette`. Keep `test_adapter_seam.py` green.

### Binding invariants
- **Invariant 4 / 5** — `list_worktrees` derives existence from `git worktree list --porcelain` (sole truth), never persists derived state; it reads live git here (not `/state`, which is the cache and Epic 2). **Invariant 10** — destructive ops (`remove --force`, `branch -D`) only via the explicit guards, never on a read path. **Invariant 12** — `remove` holds the per-repo mutex. [Source: architecture.md#Invariants]

### Closed/detached-task representation (coordinate with 1.6)
AC 5 ("task marked closed/detached" when its last worktree is removed) interacts with the status model finalized in 1.6 and the orphan rules (a `task_worktree` link whose branch is absent is shown/flagged, never auto-`done`). Implement the close/detach in a way consistent with the four-state model and the "never auto-`done` an orphan" rule; if the cleanest representation is ambiguous, prefer the minimal change and flag it for the 1.6 status work rather than inventing a new status value. [Source: architecture.md#Orphaned-link rule; epics.md#Story 1.6]

### Source tree components to touch
`tools/models.py` (`ListWorktreesIn`, `RemoveWorktreeIn`), `tools/handlers.py` (two adapters), `core/worktrees.py` (`list` + `remove`), register in `server_factory.py`; `test_worktrees.py`/`test_tools.py`. [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping]

### Project Structure Notes
No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 1.5: List and remove worktrees] — acceptance criteria
- [Source: epics.md#FR-2] list_worktrees; [Source: epics.md#FR-3] remove_worktree two-guard
- [Source: architecture.md#Removal force-flag semantics] — two distinct guards
- [Source: architecture.md#Invariants] — invariants 4, 5, 10, 12; [Source: architecture.md#Orphaned-link rule]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
