# Story 1.2: Async git execution and persistence substrate

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer building the tool,
I want the single `run_git()` helper (two pools, `-C <repo>`), the two-table machine-global `Store`, slug validation, the typed error taxonomy, and the per-repo mutation mutex,
so that every later tool shares one safe off-loop git path and one atomic persistence layer.

## Acceptance Criteria

1. **`run_git()` — two latency-class pools (AR-5).**
   **Given** a valid git repo path,
   **When** `run_git()` runs a read command,
   **Then** it executes via `create_subprocess_exec` (never shell) with pinned env (`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `-C <repo>`) under the **read pool** (3s timeout, sem=2, 2s acquire timeout);
   **And** a mutation command runs under the **mutation pool** (~120s, sem=4).

2. **Timeout/kill discipline + NotAGitRepo (AR-5, AR-8).**
   **Given** a git command that exceeds its timeout,
   **When** `run_git()` handles it,
   **Then** it kills and reaps the subprocess (no zombie), drains both pipes, and raises `GitTimeout`;
   **And** a non-git path raises `NotAGitRepo`.

3. **Slug validation (AR-9).**
   **Given** a caller-supplied task name,
   **When** it is slugified,
   **Then** the result is lowercased/hyphenated, collapses duplicate/leading/trailing hyphens, max length 60, and rejects empty/reserved/`.`/`..` with `InvalidTaskName`.

4. **Store bootstrap — two tables, machine-global (AR-6).**
   **Given** a fresh state dir,
   **When** the Store bootstraps,
   **Then** it creates `task` + `task_worktree` (PK `task_id`; PK `(task_id, repo_path)`; `ON DELETE CASCADE`; `PRAGMA foreign_keys=ON`, WAL, `busy_timeout`) at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`;
   **And** opening a DB with a newer `PRAGMA user_version` is refused with a clear error.

5. **Per-repo mutation mutex (AR-14).**
   **Given** two concurrent mutations targeting the same `repo_path`,
   **When** they run,
   **Then** the per-`repo_path` async mutex serializes them (read/refresh ops do not take it).

## Tasks / Subtasks

- [ ] **Task 1 — `errors.py`: the full typed taxonomy (AC: 2, 3; foundation for all later stories)**
  - [ ] `DevHelperError` base with `code`, `message`, `details` and `.as_dict() -> {code, message, details}`
  - [ ] One subclass per stable code: `BranchExists`, `WorktreePathInUse`, `BaseRefNotFound`, `DirtyWorktree`, `UnmergedBranch`, `TaskNotFound`, `ActiveTaskConflict`, `LockedWorktree`, `InvalidTaskName`, `GitTimeout`, `InstanceConflict`, `NotAGitRepo`, `RollbackIncomplete`, `PortUnavailable`, `Internal`. (Define them all now; later stories raise them — codes are stable contract, messages may change.)
  - [ ] `errors.py` imports nothing from `mcp`/`starlette` (core layer)
- [ ] **Task 2 — `git/runner.py`: single `run_git()` + two pools (AC: 1, 2)**
  - [ ] Two `asyncio.Semaphore`s sized from `config.py`: read (sem=2), mutation (sem=4); read pool uses a **2s acquire timeout** (fail fast / keep cache rather than queue), mutation pool generous acquire
  - [ ] `create_subprocess_exec` (never `shell`), always `-C <repo>`, pinned env (`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`), `--` end-of-options where args could be refs/paths, `-z` NUL output where parsing
  - [ ] Per-command timeout (read 3s, mutation ~120s); on timeout `proc.kill()` + `await proc.wait()` (reap, no zombie) and **drain both pipes**, then raise `GitTimeout`
  - [ ] Validate the repo path is a git repo (e.g. `git -C <repo> rev-parse --git-dir`); non-repo → `NotAGitRepo`
  - [ ] Failed git ops leave the repo unchanged (no partial side effects from the runner itself)
- [ ] **Task 3 — `git/porcelain.py`: parse `git worktree list --porcelain -z` (AC: 1)**
  - [ ] Parse NUL-delimited porcelain records into structured entries (path, head sha, branch, `detached`, `locked`, `prunable`)
  - [ ] Pure function (no I/O); total — never throws on the fixture corpus (detached HEAD, locked, prunable, unicode paths)
- [ ] **Task 4 — `core/slug.py`: task-name validation + slugify (AC: 3)**
  - [ ] Pinned regex applied **before** any shell-out; lowercase, hyphenate, collapse duplicate/leading/trailing hyphens, max length 60
  - [ ] Reject empty / reserved / `.` / `..` → raise `InvalidTaskName`; the slug is both `task_id` and the `agent/<task>` branch name
- [ ] **Task 5 — `store.py`: aiosqlite two-table Store (AC: 4)**
  - [ ] Single module that is the ONLY opener of the SQLite DB; parameterized queries only, no ORM
  - [ ] Bootstrap: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=<config>`, `PRAGMA foreign_keys=ON`; `CREATE TABLE IF NOT EXISTS` for `task` and `task_worktree` exactly per the architecture schema (status `CHECK IN ('running','blocked','review','done')`, `task_worktree` PK `(task_id, repo_path)`, `ON DELETE CASCADE`)
  - [ ] DB path `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db` (resolved in `config.py`); create parent dirs
  - [ ] Version-check migrations: read `PRAGMA user_version`; refuse to open a **newer** version with a clear error; otherwise create-if-not-exists. No migration runner
  - [ ] Provide the UPSERT + insert/select primitives later stories need (UPSERT on `task` preserves `created_at`, advances `updated_at`); do not implement tool logic here
- [ ] **Task 6 — per-repo async mutation mutex (AC: 5)**
  - [ ] A process-global registry of `asyncio.Lock` keyed by `repo_path` with an async context manager `repo_mutex(repo_path)` (suggested home: `git/runner.py` alongside the pools, or a small `core` helper — keep it core-layer, `asyncio` only)
  - [ ] Mutation orchestration acquires it; **read/refresh git ops never take it**. Lazily create one lock per distinct `repo_path`; single event loop makes a plain `dict` + `asyncio.Lock` safe
- [ ] **Task 7 — extend `config.py` (AC: 1, 4)**
  - [ ] Add pool sizes (read sem=2, mutation sem=4), timeouts (read 3s, read-acquire 2s, mutation ~120s), `busy_timeout`, and the resolved XDG state dir + `state.db`/`server.lock` paths — all in `config.py`, no magic numbers in modules
- [ ] **Task 8 — tests (`tests/` mirrors `src/`, all under the AR-12 gate)**
  - [ ] `test_git_runner.py`: timeout→kill+reap (no zombie), pool bounds (sem honored), 2s acquire timeout, pinned env, `-C <repo>`, `NotAGitRepo` on non-repo path
  - [ ] `test_porcelain.py`: parse the fixture corpus in `tests/fixtures/porcelain/` (detached HEAD, locked, prunable, unicode)
  - [ ] `test_slug.py`: valid/invalid names, collapse rules, max-60, reserved/`.`/`..` → `InvalidTaskName`
  - [ ] `test_store.py`: two-table schema + FK cascade, UPSERT (preserve `created_at`), version-check refusal on newer `user_version`, WAL enabled; use a temp/`:memory:` DB
  - [ ] `test_concurrency.py` (partial — substrate slice): two concurrent mutations on the same `repo_path` are serialized by the mutex; read/refresh ops are not blocked. (The full same-repo `create_task` race test lands in 1.3/1.4.)
  - [ ] Add `tests/fixtures/porcelain/` sample outputs and any tmp-git-repo fixture in `conftest.py`

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
This story builds the **shared substrate** every later tool sits on. Build ONLY: `run_git()`+pools, porcelain parser, slug, the full error taxonomy, the two-table Store, the per-repo mutex, and config constants. **Explicitly OUT of scope (do not pull forward):**
- **No tools** — `create_task`/`list_worktrees`/`remove_worktree`/`update_task`/`list_tasks` are Stories 1.3–1.6. Do not implement `core/tasks.py`/`core/worktrees.py` orchestration here (you provide the Store/git/slug primitives they will call).
- **No cache / projection / `/state` / dashboard** — Epic 2 (`projection.py`, `cache.py`).
- **No lockfile / single-instance protocol** — Story 3.1 (`lock.py`). This story only resolves the lockfile *path* in `config.py`; it does not create or manage `server.lock`.
- The Store **creates the schema** but performs no derive-on-read (Epic 2) and no destructive git ops.

### Builds directly on Story 1.1 (previous-story intelligence)
1.1 established the scaffold, `config.py`, `util.py` (`now_iso()`), the SDK-isolation seam (adapter vs core), the `core/`/`git/` package anchors, and the enforced AR-12 pre-commit gate. This story **fills in the core layer** under that seam:
- All new modules here (`errors.py`, `git/runner.py`, `git/porcelain.py`, `core/slug.py`, `store.py`, the mutex) are **core layer — they must import nothing from `mcp`/`starlette`.** The 1.1 adapter-seam test (`test_adapter_seam.py`) now has real modules to scan; keep it green.
- Reuse `now_iso()` from `util.py` for all timestamps (created_at/updated_at). Do not call `datetime.now()`.
- Extend the existing `config.py`; do not scatter new constants.

### Binding invariants this story establishes (architecture.md § Invariants)
- **Invariant 1 — every git call goes through the single `run_git()` and its correct pool.** No `subprocess`/`os.system` for git anywhere else, ever. [Source: architecture.md#Invariants; #Structure & Process Patterns]
- **Invariant 4 — derive-on-read; never persist derived state.** The Store holds ONLY `task` + `task_worktree` records; git porcelain is the sole truth for worktree existence. Do not add derived/health/orphan columns. [Source: architecture.md#Invariants; #Data Architecture]
- **Invariant 6 — no blocking call on the event loop.** Git off-loop via `run_git` (`create_subprocess_exec`); DB via `aiosqlite`; any unavoidable sync work via `asyncio.to_thread`. [Source: architecture.md#Invariants]
- **Invariant 11 — timestamps via the single `now_iso()` helper** (UTC ISO-8601 `Z`, second precision). [Source: architecture.md#Invariants]
- **Invariant 12 — same-repo mutations serialized by a per-`repo_path` async mutex**; the global lockfile (3.1) guards only the process singleton, not per-repo safety. Read/refresh ops do not take the mutex. [Source: architecture.md#Invariants; #Per-repo mutation mutex]

### Exact schema (copy precisely — architecture.md § Data Architecture)
```sql
CREATE TABLE IF NOT EXISTS task (
  task_id     TEXT PRIMARY KEY,                  -- the <task> slug; one active task per slug
  description TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('running','blocked','review','done')),
  created_at  TEXT NOT NULL,                     -- UTC ISO-8601
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_worktree (
  task_id       TEXT NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
  repo_path     TEXT NOT NULL,                   -- absolute repo path
  branch        TEXT NOT NULL,                   -- agent/<task>
  worktree_path TEXT NOT NULL,
  PRIMARY KEY (task_id, repo_path)               -- one worktree per repo per task
);
```
`PRAGMA foreign_keys=ON` at bootstrap so the cascade holds (SQLite defaults it OFF per-connection — set it on every connection). [Source: architecture.md#Data Architecture]

### `run_git()` contract details (the highest-divergence-risk operation)
- Two latency classes, **separate semaphore pools**: read/refresh (`worktree list`, status, commit-count) = 3s timeout / sem=2 / **2s acquire** (fail fast, raise rather than queue — protects the ≤3s read SLA); mutation (`worktree add/remove`, `branch -d/-D`) = ~120s / sem=4 (a cold/large checkout is legitimately multi-second, stays under the ~5-min transport ceiling). [Source: architecture.md#Async-git execution]
- Always `create_subprocess_exec` (no shell), pinned env, `-C <repo>`, `-z` porcelain parsing, `--` end-of-options against argument injection. On timeout: `kill()` + `await wait()` (no zombies), drain both pipes. [Source: architecture.md#Structure & Process Patterns; #Async-git execution]

### Conventions (architecture.md § Implementation Patterns)
- `snake_case` functions/vars/modules; `PascalCase` classes; `UPPER_SNAKE` constants; type hints on every public signature; module names are nouns. [Source: architecture.md#Naming Patterns]
- Core logic **raises typed `DevHelperError`** — never returns ad-hoc error dicts (the adapter builds the envelope in later stories). [Source: architecture.md#Result Envelope & Error Patterns]
- `logging.getLogger(__name__)` per module; level from `DEV_HELPER_LOG`; never log secrets or full annotation contents at `INFO`. [Source: architecture.md#Structure & Process Patterns]

### Source tree components to touch (this story)
Creates/extends: `errors.py`, `git/runner.py`, `git/porcelain.py`, `core/slug.py`, `store.py`, the per-repo mutex helper, extends `config.py`; adds `tests/fixtures/porcelain/` + the listed test modules. [Source: architecture.md#Complete Project Directory Structure]
Data boundary to honor: `store.py` is the ONLY module that opens the SQLite DB; `git/runner.py` is the ONLY module that spawns `git`. [Source: architecture.md#Architectural Boundaries]

### Project Structure Notes
- Runtime state (`state.db`, later `server.lock`) lives at the machine-global XDG path, created at runtime — never under `src/`, never in-repo. [Source: architecture.md#Runtime state]
- This story is a strict subset of the architecture's defined tree; no structural variance.

### Latest tech / version notes
- `aiosqlite` (event-loop-safe SQLite) — installed in 1.1, first used here. WAL + `busy_timeout` are required because a polling dashboard (Epic 2) reads while tools write. [Source: architecture.md#Data Architecture]
- `asyncio` stdlib for pools/mutex; no third-party concurrency lib.
- No git library — the `git` CLI via `create_subprocess_exec` only. [Source: architecture.md#Technical Constraints]

### References
- [Source: epics.md#Story 1.2: Async git execution and persistence substrate] — acceptance criteria
- [Source: epics.md#AR-5] run_git/pools; [Source: epics.md#AR-6] persistence; [Source: epics.md#AR-8] error taxonomy; [Source: epics.md#AR-9] slug rules; [Source: epics.md#AR-14] per-repo mutex
- [Source: architecture.md#Invariants] — invariants 1, 4, 6, 11, 12
- [Source: architecture.md#Data Architecture] — exact schema, PRAGMAs, version-check
- [Source: architecture.md#Async-git execution] — two-pool timeouts/semaphores/acquire
- [Source: architecture.md#Architectural Boundaries] — store/runner single-opener boundaries

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
