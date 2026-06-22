# Story 1.3: Create a multi-repo task (happy path)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent,
I want to create a task spanning one or more repositories in a single `create_task` call,
so that each repo gets its own isolated worktree + `agent/<task>` branch for my unit of work.

## Acceptance Criteria

1. **Single-repo create (FR-1, FR-4).**
   **Given** one valid repo path `A` and a task name,
   **When** I call `create_task(task_name, description, repos=[A])`,
   **Then** a worktree is created at `<A-parent>/<A>.worktrees/<task>/` on branch `agent/<task>` from A's HEAD, a `task` row (status `running`) and one `task_worktree` row are persisted, and it returns `{ok:true, data:{task_id, status, worktrees:[{repo_path, worktree_path, branch}]}}` (snake_case, `now_iso()` timestamps).

2. **Multi-repo create, all succeed — rows last (FR-1, AR-13 ordering).**
   **Given** several valid repos `[A,B,C]` that all succeed,
   **When** I call `create_task`,
   **Then** one worktree + `agent/<task>` branch is created per repo and one `task_worktree` row per repo is committed in a **single SQLite transaction** (rows written **last**, after all worktrees succeed).

3. **Optional `base_ref`.**
   **Given** an optional `base_ref`,
   **When** provided,
   **Then** each repo's worktree is created from that ref (which must exist in every requested repo; absence → `BaseRefNotFound`).

4. **Active-task conflict — `status != 'done'` (FR-4; highest-risk seam of the 4-status change).**
   **Given** an active task already using the same `<task>` slug,
   **When** `create_task` is called,
   **Then** `ActiveTaskConflict` is returned and nothing is created — where **"active" is defined as `status != 'done'`**, so `running`, `blocked`, **and `review`** all conflict (regression test: `create → update_task(review) → create same slug` rejects; `create → update_task(done) → create same slug` succeeds). The predicate must NOT be an enumerated allowlist that silently omits `review`.

5. **Preflight collisions before any mutation (FR-1, AR-13 preflight).**
   **Given** the `agent/<task>` branch or target directory already exists in any requested repo (preflight),
   **When** `create_task` is called,
   **Then** `BranchExists` / `WorktreePathInUse` is returned **before any repo is mutated**.

## Tasks / Subtasks

- [ ] **Task 1 — `tools/models.py`: `CreateTaskIn` (AC: 1, 3)**
  - [ ] Pydantic `CreateTaskIn{task_name: str, description: str, repos: list[str] (1+), base_ref: str | None}` at the adapter boundary; validation + advertised JSON schema come from the model; snake_case fields
- [ ] **Task 2 — `core/worktrees.py`: per-repo worktree creation (AC: 1, 3, 5)**
  - [ ] `add(repo_path, branch, worktree_path, base_ref)` → `git -C <repo> worktree add -b agent/<task> <worktree_path> <base_ref|HEAD>` via the **mutation pool**; plain typed args, raises `DevHelperError`
  - [ ] Worktree path convention exactly `<repo-parent>/<repo-basename>.worktrees/<task>/`
  - [ ] Per-repo preflight helpers: branch-exists check → `BranchExists`; target dir exists → `WorktreePathInUse`; base ref missing → `BaseRefNotFound`; non-git repo → `NotAGitRepo` (from 1.2 `run_git`)
- [ ] **Task 3 — `core/tasks.py`: `create()` orchestration, happy path (AC: 1, 2, 4, 5)**
  - [ ] **Preflight ALL repos first** (git repo? `agent/<task>` branch/dir free? base ref exists in each?) before mutating ANY repo — cheapest rollback is not starting
  - [ ] One-active-per-slug check using predicate **`status != 'done'`** (query `task` by `task_id`); active → `ActiveTaskConflict`. A prior `done` task with the same slug is overwritten via UPSERT (preserve `created_at`)
  - [ ] Acquire the **per-repo mutex** (from 1.2) for each touched `repo_path` before its mutation; provision all worktrees via the mutation pool
  - [ ] On full success: write the `task` row (status `running`) + one `task_worktree` row per repo **LAST**, in a single SQLite transaction; timestamps via `now_iso()`
  - [ ] Return the plain result dict `{task_id, status, worktrees:[{repo_path, worktree_path, branch}]}` (core returns data; the adapter wraps the envelope)
- [ ] **Task 4 — `tools/handlers.py` + register in `server_factory.py` (AC: 1)**
  - [ ] `create_task` adapter: validate `CreateTaskIn` → call `core.tasks.create(...)` with plain args → wrap `{ok, data}`; catch `DevHelperError` → `{ok:false, error: e.as_dict()}`; unexpected → `{code:"Internal"}` (never leak a stack trace)
  - [ ] Register the real `create_task` tool on the FastMCP instance (the 1.1 `ping` no-op may remain until 1.6 completes the surface)
- [ ] **Task 5 — tests (under the AR-12 gate; tmp git repos)**
  - [ ] `test_tasks.py` (happy path slice): single-repo create (row + worktree + branch + return shape); multi-repo all-succeed (rows committed last, one per repo, single transaction); `base_ref` honored
  - [ ] Active-conflict regression: `create → update stub/direct status=review → create same slug` rejects with `ActiveTaskConflict`; `... status=done → create same slug` succeeds (assert predicate is `!= 'done'`, not an allowlist)
  - [ ] Preflight: pre-existing `agent/<task>` branch → `BranchExists` before any mutation; pre-existing target dir → `WorktreePathInUse`; missing `base_ref` → `BaseRefNotFound`
  - [ ] `test_tools.py` (slice): `create_task` envelope shape + snake_case keys + error-as-data
  - [ ] `test_concurrency.py`: two `create_task` calls touching a shared repo do not race (per-repo mutex serializes the `git worktree add`)

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
This is the **happy path + preflight + active-conflict** for `create_task`. **Explicitly OUT of scope (do not pull forward):**
- **No error-safe rollback / compensation** — partial-failure teardown (`worktree remove --force` + `branch -D`), the `RepoMutator` seam, `FlakyMutator` fault injection, and `RollbackIncomplete` are **Story 1.4**. Here, assume the "all succeed" path; you implement preflight (don't-start rollback) and correct rows-last ordering, but **not** the mid-flight compensation. (Structure `create()` so 1.4 can insert compensation cleanly — see note below.)
- **No `list_worktrees` / `remove_worktree`** — Story 1.5.
- **No `update_task` / `list_tasks`** — Story 1.6 (this story may use a direct Store status write in tests to set up the active-conflict cases; the real `update_task` tool is 1.6).
- **No cache / dashboard** — Epic 2.

### Structure for 1.4's benefit (forward-compatibility)
Write `create()` so the per-repo mutation step is a clean, swappable seam — 1.4 introduces a `RepoMutator` interface (real vs `FlakyMutator`) and wraps the provisioning loop with reverse-order compensation. Keep "provision worktrees" as a discrete, list-producing step (track which repos succeeded) so compensation in 1.4 can iterate the succeeded set in reverse. Do NOT bake in a different shape that 1.4 must tear apart.

### Builds on Stories 1.1 + 1.2 (previous-story intelligence)
- From **1.2**: use `run_git()` (mutation pool) for `worktree add`; `core/slug.py` to derive `task_id`/`agent/<task>`; `store.py` for the task/task_worktree writes + UPSERT; `errors.py` codes; the **per-repo mutex**; `now_iso()`. Do not re-implement any of these.
- From **1.1**: the adapter seam — `core/tasks.py` and `core/worktrees.py` import nothing from `mcp`/`starlette`; only `tools/handlers.py` and `server_factory.py` touch the SDK. Keep `test_adapter_seam.py` green.
- The AR-12 pre-commit gate is live — all new tests run under it.

### Binding invariants (architecture.md § Invariants)
- **Invariant 1** — `worktree add` only via `run_git()` mutation pool. **Invariant 2** — every tool returns `{ok,data,error}`; core raises typed errors. **Invariant 3** — snake_case JSON keys. **Invariant 12** — same-repo mutations hold the per-repo mutex; `create_task` writes DB rows **last** in one transaction. [Source: architecture.md#Invariants]

### `create_task` ordering (pinned — architecture.md § create_task atomicity, AR-13)
1. Preflight ALL repos (git repo? branch/dir free? base ref?) — raise before mutating any.
2. Provision all worktrees (mutation pool, per-repo mutex held).
3. Write `task` + `task_worktree` rows LAST, single SQLite transaction, only on full success.
A crash before the commit leaves NO DB rows (residue = an orphan worktree surfaced later by derive-on-read; crash-safety is a documented v1 non-goal). [Source: architecture.md#API & Communication Patterns]

### Tool I/O contract
`create_task` inputs: `task_name`, `description`, `repos:[abs_path,…]` (1+), `base_ref?` (default each repo's HEAD). Output `{task_id, status, worktrees:[{repo_path, worktree_path, branch}]}`. The agent branches on `error.code`. [Source: architecture.md#API & Communication Patterns; #Examples]

### Source tree components to touch
`tools/models.py` (`CreateTaskIn`), `tools/handlers.py` (`create_task` adapter), `core/tasks.py` (`create`), `core/worktrees.py` (per-repo `add` + preflight), register in `server_factory.py`; tests `test_tasks.py`/`test_tools.py`/`test_concurrency.py`. [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping]

### Project Structure Notes
- Worktrees live as **siblings of each repo** (`<repo-parent>/<repo>.worktrees/<task>/`), never under `src/` or the XDG state dir. [Source: architecture.md#Runtime state]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 1.3: Create a multi-repo task (happy path)] — acceptance criteria
- [Source: epics.md#FR-1] create_task; [Source: epics.md#FR-4] register/one-active-per-slug; [Source: epics.md#AR-13] atomicity/ordering; [Source: epics.md#AR-11] Pydantic `*In` models
- [Source: architecture.md#API & Communication Patterns] — create_task inputs/outputs, atomicity ordering, slug rules
- [Source: architecture.md#Examples] — tool-adapter good pattern; [Source: architecture.md#Invariants] — invariants 1,2,3,12

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
