# Story 1.6: Update and list tasks (status lifecycle) and complete the tool surface

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent,
I want to update a task's status and description and query tasks by status or repo,
so that I can self-report progress and a monitoring view can reflect it.

## Acceptance Criteria

1. **`update_task` — status/description, bump `updated_at`, four-state set (FR-5, FR-6).**
   **Given** an existing task,
   **When** I call `update_task(task_id, status?, description?)`,
   **Then** the status and/or description are updated and `updated_at` is bumped;
   **And** a status outside the four-state set {`running`,`blocked`,`review`,`done`} is rejected (`blocked`=awaiting input, `review`=awaiting review).

2. **Full 4×4 transition matrix — `done` is terminal (FR-6; spec-gap closed in Party Mode).**
   **Given** the status transition graph,
   **When** `update_task` changes status,
   **Then** transitions among `running` ↔ `blocked` ↔ `review` are all legal, and **`done` is terminal** — a task in `done` cannot be moved back to an active status (re-activating a slug is done via a new `create_task`, not `update_task`); the full 4×4 transition matrix is enforced (legal set passes, illegal `done → *` rejects), asserted by a parametrized table test.

3. **Not-found (FR-5, AR-8).**
   **Given** a non-existent `task_id`,
   **When** `update_task` is called,
   **Then** `TaskNotFound` is returned.

4. **`done` → slug reusable + flagged closed (FR-4, FR-6).**
   **Given** a task updated to `done`,
   **When** it completes,
   **Then** it no longer counts as the active task for its `<task>` slug (the slug becomes reusable) and is flagged closed (moves to the folded Done section on the dashboard).

5. **`list_tasks` — filter + all model fields incl. per-repo links (FR-7).**
   **Given** existing tasks,
   **When** I call `list_tasks(status?, repo?)`,
   **Then** the matching tasks are returned with all model fields including their per-repo `(repo_path, branch, worktree_path)` links.

6. **Exactly 5 tools advertised, uniform envelope (FR-11).**
   **Given** a connected MCP client,
   **When** it enumerates the tool surface,
   **Then** exactly **5** tools are advertised with input/output schemas — `create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks` — all returning the `{ok,data,error}` envelope with snake_case keys.

## Tasks / Subtasks

- [ ] **Task 1 — `tools/models.py`: `UpdateTaskIn` + `ListTasksIn` (AC: 1, 5)**
  - [ ] `UpdateTaskIn{task_id: str, status: str | None, description: str | None}`
  - [ ] `ListTasksIn{status: str | None, repo: str | None}`
- [ ] **Task 2 — `core/tasks.py`: `update()` with the transition matrix (AC: 1, 2, 3, 4)**
  - [ ] Load the task by `task_id`; absent → `TaskNotFound`
  - [ ] Validate `status` (if given) ∈ {`running`,`blocked`,`review`,`done`}; otherwise reject (rely on the model + an explicit guard; the DB `CHECK` is the backstop)
  - [ ] Enforce the **4×4 transition matrix**: `running`/`blocked`/`review` interchange freely; **`done` is terminal** — any `done → {running,blocked,review}` is rejected. Define a single source-of-truth legal-transition set/table
  - [ ] Update status and/or description; bump `updated_at` via `now_iso()` (preserve `created_at`)
  - [ ] On transition to `done`: the slug is no longer the active task (UPSERT/`create_task` may reuse it) and the task is flagged closed
- [ ] **Task 3 — `core/tasks.py`: `list()` (AC: 5)**
  - [ ] Query `task` filtered by `status`; when `repo` given, filter to tasks having a `task_worktree` row for that repo
  - [ ] Return all model fields + per-repo `(repo_path, branch, worktree_path)` links per task
- [ ] **Task 4 — `tools/handlers.py` + finalize the surface in `server_factory.py` (AC: 6)**
  - [ ] `update_task` + `list_tasks` adapters: validate → core → `{ok,data,error}`; map codes
  - [ ] **Finalize exactly 5 tools** registered: `create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks`. **Remove the throwaway `ping`/health tool** from Story 1.1 so the advertised surface is exactly 5
- [ ] **Task 5 — tests (under AR-12 gate)**
  - [ ] `test_tasks.py` (update/list slice): update status + description bumps `updated_at`; invalid status rejected; `TaskNotFound`
  - [ ] **Parametrized 4×4 transition table test**: every legal transition among running/blocked/review passes; every `done → *` is rejected; document the matrix in the test
  - [ ] `done` reusability: `create → update(done) → create same slug` succeeds (cross-check with 1.3's `ActiveTaskConflict` regression — `review` still conflicts, `done` does not)
  - [ ] `list_tasks`: filter by `status` and by `repo`; assert all model fields + per-repo links present
  - [ ] `test_server_factory.py` (or `test_tools.py`): enumerate tools → assert **exactly 5** names + each advertises input/output schema + every result is the `{ok,data,error}` envelope with snake_case keys

## Dev Notes

### Scope boundaries — read first
Completes Epic 1's tool surface (the last two tools + the status lifecycle) and locks the advertised surface to exactly 5. **OUT of scope:** the cache/projection/`/state`/dashboard that *renders* status as columns (Epic 2 — this story makes the status model the dashboard will read, but builds no UI); single-instance/lockfile/install (Epic 3). The dashboard's "folded Done" rendering is 2.4a — here you only ensure `done` is terminal and flagged closed in the model.

### The two highest-risk seams in this story (Party Mode flags)
1. **`done` is terminal** — the transition policy was a spec gap; the resolution is `done → *` rejected by `update_task`. Re-activation of a slug happens ONLY via a new `create_task` (UPSERT after done), never via `update_task`. Build a single legal-transition table and test all 16 cells. [Source: epics.md#Story 1.6; architecture.md AMENDMENT 2026-06-22b §7]
2. **Active = `status != 'done'`** (set in 1.3) — `update_task(review)` keeps the task active (still conflicts on same-slug `create_task`); `update_task(done)` releases the slug. Cross-check this story's `done`-reusability test against 1.3's `ActiveTaskConflict` regression so the two predicates stay consistent. [Source: epics.md#Story 1.3 AC4]

### Builds on Stories 1.2–1.5 (previous-story intelligence)
- From **1.2**: `store.py` (update/select on `task`; per-repo link select), `errors.py` (`TaskNotFound`, `ActiveTaskConflict`), `now_iso()`, the status `CHECK` constraint as the DB backstop.
- From **1.3/1.5**: `core/tasks.py` already has `create`/(close-detach hooks); extend with `update`/`list`. Reuse the `(repo_path, branch, worktree_path)` link shape.
- Adapter seam holds: `core/tasks.py` imports no `mcp`/`starlette`; only `tools/handlers.py` + `server_factory.py` touch the SDK. Keep `test_adapter_seam.py` green.

### Status model (architecture.md AMENDMENT §7, AR-6, FR-6)
Four states, **one status per task** across all its repos: `running` (working) · `blocked` (awaiting user input) · `review` (agent finished, awaiting the operator's review — active, non-`done`; the tool does NOT merge) · `done` (reviewed/closed, terminal). Reason badges read "needs input"/"awaiting review" — never "merge". `done` is visually distinguished (dimmed/folded) on the dashboard (Epic 2). [Source: epics.md#FR-6; architecture.md#AMENDMENT 2026-06-22; #Data Architecture]

### Binding invariants
- **Invariant 2** — every tool returns `{ok,data,error}`; core raises typed errors. **Invariant 3** — snake_case keys. **Invariant 11** — `updated_at` via `now_iso()`. The 5-tool surface is a public contract (FR-11; well under the ~40 client cap). [Source: architecture.md#Invariants; #API & Communication Patterns]

### Source tree components to touch
`tools/models.py` (`UpdateTaskIn`, `ListTasksIn`), `tools/handlers.py` (two adapters + drop `ping`), `core/tasks.py` (`update` + `list` + legal-transition table), finalize registration in `server_factory.py`; `test_tasks.py`/`test_tools.py`/`test_server_factory.py`. [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping]

### Project Structure Notes
- Removing the 1.1 `ping` tool is the one deliberate regression to a prior story's artifact — it was always documented as a throwaway health tool. Confirm the 1.1 transport/lifespan tests still pass without it (they should assert handshake + a real tool round-trip; update any test that specifically round-tripped `ping` to use a real tool instead).
- No other structural variance from the architecture tree.

### References
- [Source: epics.md#Story 1.6: Update and list tasks (status lifecycle) and complete the tool surface] — acceptance criteria
- [Source: epics.md#FR-5] update_task; [Source: epics.md#FR-6] four-state set + done-terminal; [Source: epics.md#FR-7] list_tasks; [Source: epics.md#FR-11] 5-tool surface
- [Source: architecture.md#AMENDMENT 2026-06-22] §7 four-state model; [Source: architecture.md#Data Architecture] status CHECK + UPSERT-after-done
- [Source: architecture.md#Invariants] — invariants 2, 3, 11

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
