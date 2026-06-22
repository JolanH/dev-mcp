---
baseline_commit: 9e33106da66c9d70e8c1adc17f62c9159705d5de
---

# Story 1.2: Async git execution and persistence substrate

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer building the tool,
I want the single `run_git()` helper (two pools, `-C <repo>`), the two-table machine-global `Store`, slug validation, the typed error taxonomy, and the per-repo mutation mutex,
so that every later tool shares one safe off-loop git path and one atomic persistence layer.

## Acceptance Criteria

1. **`run_git()` — single helper, two pools (Invariant 1; AR / arch §Async-git).**
   **Given** a valid git repo path,
   **When** `run_git()` runs a **read** command,
   **Then** it executes via `asyncio.create_subprocess_exec` (**never** a shell) with pinned env (`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`) and `-C <repo>`, under the **read pool** (3s per-command timeout, semaphore=2, **2s acquire timeout** — fail fast rather than queue);
   **And** a **mutation** command runs under the **mutation pool** (~120s timeout, semaphore=4).

2. **Timeout → kill + reap, no zombie; non-repo → typed error.**
   **Given** a git command that exceeds its timeout,
   **When** `run_git()` handles it,
   **Then** it kills and reaps the subprocess (no zombie left behind), drains both stdout/stderr pipes, and raises `GitTimeout`;
   **And** pointing the git-execution layer at a path that is **not a git repository** raises `NotAGitRepo`.

3. **Slug validation + slugify (Invariant — `core/slug.py`).**
   **Given** a caller-supplied task name,
   **When** it is slugified,
   **Then** the result is lowercased and hyphenated, collapses duplicate / leading / trailing hyphens, is capped at **max length 60**, and **rejects** empty / reserved / `.` / `..` inputs with `InvalidTaskName`.

4. **Store bootstrap — two tables, pragmas, version-check (Invariant 4; arch §Data Architecture).**
   **Given** a fresh state dir,
   **When** the `Store` bootstraps,
   **Then** it creates `task` + `task_worktree` (`task` PK `task_id`; `task_worktree` PK `(task_id, repo_path)`; FK `ON DELETE CASCADE`; `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`) at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`;
   **And** opening a DB whose `PRAGMA user_version` is **newer** than this build's schema version is refused with a clear error (never silently downgraded or migrated).

5. **Per-repo async mutation mutex (Invariant 12; arch §Per-repo mutation mutex).**
   **Given** two concurrent mutations targeting the same `repo_path`,
   **When** they run,
   **Then** a per-`repo_path` async mutex serializes them (the second waits for the first to release);
   **And** read/refresh git ops do **not** take this mutex.

## Tasks / Subtasks

> **Build order (each task is independently testable; do them in this order so tests can build up):**
> errors → config constants → slug → run_git → repo-mutex → Store. No MCP tool, no porcelain parsing, no dashboard — see **Scope boundaries**.

- [x] **Task 1 — `errors.py`: the full typed error taxonomy (AC: 2, 3; Invariant 2)**
  - [x] Create `src/dev_helper_mcp/errors.py` (core layer — **no `mcp`/`starlette` import**) with a base `DevHelperError(Exception)` carrying `code: str`, `message: str`, `details: dict | None`, and an `.as_dict()` method returning `{"code", "message", "details"}` (omit or `null` `details` consistently — prefer `{}`/omit per the Data & Format pattern).
  - [x] Define **one subclass per code** for the **complete, stable taxonomy** (this is the substrate — later stories only *raise* these, never add ad-hoc dicts): `BranchExists`, `WorktreePathInUse`, `BaseRefNotFound`, `DirtyWorktree`, `UnmergedBranch`, `TaskNotFound`, `ActiveTaskConflict`, `LockedWorktree`, `InvalidTaskName`, `GitTimeout`, `InstanceConflict`, `NotAGitRepo`, `RollbackIncomplete`, `PortUnavailable`, `Internal`. Each subclass sets its own `code`. `code` is contract; `message` may vary.
  - [x] Only `GitTimeout`, `NotAGitRepo`, `InvalidTaskName` are *exercised* by this story; the rest are defined for downstream stories (1.3–1.6, 3.x). Do **not** wire the envelope conversion here — that is `tools/handlers.py` (Story 1.3+).
- [x] **Task 2 — extend `config.py` with substrate tunables (AC: 1, 4; arch §File Organization — all tunables in config.py)**
  - [x] Add the two-pool constants: `GIT_READ_TIMEOUT = 3.0`, `GIT_READ_POOL_SIZE = 2`, `GIT_READ_ACQUIRE_TIMEOUT = 2.0`, `GIT_MUTATION_TIMEOUT = 120.0`, `GIT_MUTATION_POOL_SIZE = 4`.
  - [x] Add the pinned git env mapping: `GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}` (merged over `os.environ` at spawn).
  - [x] Add slug rules: `SLUG_MAX_LENGTH = 60` and the reserved-name set (e.g. `{"", ".", ".."}`; extend if other reserved slugs are obvious).
  - [x] Add persistence paths + schema version: a `state_dir()` / `default_db_path()` resolver honoring `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/`, `STATE_DB_NAME = "state.db"`, and `SCHEMA_VERSION = 1`. Keep the resolver a pure function so tests can override the dir.
  - [x] No magic numbers anywhere else — every timeout/pool/limit lives here.
- [x] **Task 3 — `core/slug.py`: validate + slugify (AC: 3)**
  - [x] Create `src/dev_helper_mcp/core/slug.py` (pure; `re` only; **no SDK import**). Public `slugify(name: str) -> str` raising `InvalidTaskName` on reject.
  - [x] Rules: lowercase; replace runs of non-`[a-z0-9]` with a single `-`; collapse duplicate hyphens; strip leading/trailing hyphens; enforce `len <= SLUG_MAX_LENGTH`; reject empty result and reserved/`.`/`..`. Decide truncation-vs-reject for over-length: **reject** with `InvalidTaskName` (don't silently truncate — a truncated slug could collide; matches "no silent suffixing" intent).
  - [x] The returned slug is the `task_id` **and** the `agent/<task>` branch name reused across every repo (callers in 1.3 rely on this) — keep it filesystem- and git-ref-safe.
- [x] **Task 4 — `git/runner.py`: the single `run_git()` + two pools (AC: 1, 2)**
  - [x] Create `src/dev_helper_mcp/git/runner.py` (core layer — imports `asyncio`/`os`/stdlib only; **no `mcp`/`starlette`**). Expose `run_git(repo: str, args: Sequence[str], *, pool: <read|mutation>)` — a single helper; the pool is an explicit required argument (an enum or two thin wrappers `run_read`/`run_mutation` over one core impl). **Never** add a second git entry point anywhere in the codebase (Invariant 1).
  - [x] Spawn with `create_subprocess_exec("git", "-C", repo, *args, env={**os.environ, **GIT_ENV}, stdout=PIPE, stderr=PIPE)` — **no `shell=True`, never `subprocess.run`**. Return a small result (`returncode: int`, `stdout: bytes`, `stderr: bytes`) — keep stdout **bytes** (later `-z` NUL porcelain parsing needs raw bytes; callers decode). Non-zero return codes are **returned**, not raised (callers classify e.g. `BranchExists`); only `GitTimeout` and `NotAGitRepo` are raised by this layer.
  - [x] **Timeout/reap pattern (use exactly this):** `out, err = await asyncio.wait_for(proc.communicate(), timeout)`; on `TimeoutError` → `proc.kill(); await proc.wait()`; then `raise GitTimeout(...)`. `communicate()` drains both pipes (no pipe-buffer deadlock); `kill()`+`wait()` reaps so no zombie.
  - [x] **Pools as live-loop objects, NOT import-time globals** (critical gotcha — see Dev Note "asyncio objects + asyncio.run() per test"). Encapsulate the two `asyncio.Semaphore`s in a small holder/`GitRunner` constructed inside the running loop (app startup / test setup), or lazily create per loop. Read pool: `await asyncio.wait_for(sem.acquire(), GIT_READ_ACQUIRE_TIMEOUT)` → on `TimeoutError` raise `GitTimeout` (fail-fast, don't queue); release in `finally`. Mutation pool: plain `await sem.acquire()` (generous), release in `finally`.
  - [x] **`NotAGitRepo`:** the git-execution layer must surface a non-repo path as `NotAGitRepo`. Recommended: a sibling preflight `require_git_repo(repo)` in this module that runs `rev-parse --is-inside-work-tree` (or `--git-dir`) via the read pool and raises `NotAGitRepo` on failure / missing path; `create_task` preflight (1.3/1.4) will call it. (Alternatively classify git's canonical `fatal: not a git repository` / `-C` chdir failure inside `run_git`.) Test asserts `NotAGitRepo` for a non-repo dir either way.
- [x] **Task 5 — per-`repo_path` async mutation mutex (AC: 5; Invariant 12)**
  - [x] Provide a small registry returning one `asyncio.Lock` per `repo_path` (e.g. `RepoLockRegistry.lock_for(repo_path) -> asyncio.Lock`), used as `async with registry.lock_for(repo): ...` around same-repo mutations. **Read/refresh ops never take it.**
  - [x] **Module home is not pinned in the architecture tree** — place it in `git/repo_lock.py` (pairs with the mutation pool, core-layer, SDK-free). Flag this in Project Structure Notes as a deliberate addition; `core/` may import `git/` (both SDK-free) so `core/tasks.py` (1.3) can use it.
  - [x] Same live-loop caution as the pools: locks must be created within the running loop; the registry's `dict[str, asyncio.Lock]` must not be a stale import-time global reused across `asyncio.run()` calls.
  - [x] **No real mutation exists yet** (`create_task` is Story 1.3). Test the primitive directly: two coroutines entering `lock_for(SAME_PATH)` are serialized (a shared list / sleep proves interleave-free ordering); two coroutines on *different* paths run concurrently.
- [x] **Task 6 — `store.py`: aiosqlite Store bootstrap + schema + version-check (AC: 4)**
  - [x] Create `src/dev_helper_mcp/store.py` (core layer — imports `aiosqlite`, **not** `mcp`/`starlette`; the adapter-seam test already scans `store.py`). Accept a `db_path` argument (default from `config.default_db_path()`); tests pass a tmp file or `:memory:`.
  - [x] On bootstrap (in this exact order): ensure the parent dir exists; open; `PRAGMA journal_mode=WAL`; `PRAGMA busy_timeout=<ms>`; `PRAGMA foreign_keys=ON`; **version-check** (`PRAGMA user_version` — if `> SCHEMA_VERSION` raise a clear error and refuse; if `0`/fresh, run `CREATE TABLE IF NOT EXISTS` for both tables then `PRAGMA user_version = SCHEMA_VERSION`); `CREATE TABLE IF NOT EXISTS` is idempotent on re-open.
  - [x] Schema exactly as the architecture pins it:
    ```sql
    CREATE TABLE IF NOT EXISTS task (
      task_id     TEXT PRIMARY KEY,
      description TEXT NOT NULL,
      status      TEXT NOT NULL CHECK (status IN ('running','blocked','review','done')),
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS task_worktree (
      task_id       TEXT NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
      repo_path     TEXT NOT NULL,
      branch        TEXT NOT NULL,
      worktree_path TEXT NOT NULL,
      PRIMARY KEY (task_id, repo_path)
    );
    ```
  - [x] Use **parameterized queries only**, no ORM. Provide just the low-level methods the Store owns; full CRUD/UPSERT is *exercised* by `create_task` (1.3) — for this story deliver bootstrap + schema + version-check + enough primitives to prove FK cascade. Do **not** implement task-conflict logic (1.3) or derive-on-read (Epic 2).
- [x] **Task 7 — tests (`tests/` mirrors `src/`; `test_<module>.py`) (AC: 1–5)**
  - [x] `tests/test_slug.py` — valid names → expected slug; collapse/strip/lowercase cases; over-length, empty, `.`/`..`, reserved → `InvalidTaskName`.
  - [x] `tests/test_git_runner.py` — read command on a real tmp git repo succeeds via read pool; a deliberately slow/hanging command exceeds timeout → `GitTimeout` with no orphaned child (assert process reaped); pinned env present (`-C` applied); non-repo dir → `NotAGitRepo`; read-pool **acquire** saturation raises `GitTimeout` (fail-fast). Build a throwaway repo with `run_git` mutation or a `conftest` `tmp_git_repo` fixture (`git init -q`, one commit). Drive async tests with `asyncio.run()` — **no `pytest-asyncio`**.
  - [x] `tests/test_concurrency.py` — per-repo mutex serializes two coroutines on the same path; different paths run concurrently. (Real same-repo `create_task`/`remove_worktree` racing is added in 1.3/1.4; here it's the primitive.)
  - [x] `tests/test_store.py` — fresh bootstrap creates both tables; `foreign_keys=ON` + `ON DELETE CASCADE` actually cascades (`DELETE FROM task` removes its `task_worktree` rows); `journal_mode` is `wal` (**use a tmp file DB, not `:memory:`, for the WAL assertion**); newer `user_version` → refused with clear error; re-open is idempotent.
  - [x] `tests/test_errors.py` — `DevHelperError.as_dict()` shape `{code, message, details}`; each subclass carries its correct stable `code`.
  - [x] Add a `tmp_git_repo` fixture to `tests/conftest.py` if not present (reused by 1.3–1.5). Keep the existing in-process ASGI fixtures untouched.
  - [x] **All tests must pass under the enforced pre-commit gate** (`ruff check` + `ruff format --check` + `pytest -m "not slow"`) established in Story 1.1.

### Review Findings

_Code review 2026-06-22 (adversarial: Blind Hunter + Edge Case Hunter + Acceptance Auditor). All 5 ACs CONFIRMED met; error taxonomy matches the architecture's frozen 15-code set; SDK seam holds; scope fence respected. 8 findings dismissed as noise._

- [x] [Review][Decision] Version-check refusal raises `Internal` — RESOLVED 2026-06-22: **keep `Internal` as-is**. The refusal fires only at server startup (`Store.open` at boot), never inside a tool call, so it never reaches a `{ok,error}` envelope; the message is clear and the taxonomy stays frozen. Accepted, no change. [src/dev_helper_mcp/store.py:_bootstrap]
- [x] [Review][Patch] RepoLockRegistry keys on the raw `repo_path` string — equivalent paths (trailing slash, `.`/`..`, relative) mint different locks, defeating the per-repo mutex it exists to provide (Invariant 12) [src/dev_helper_mcp/git/repo_lock.py:lock_for]
- [x] [Review][Patch] `run_git` orphans the git subprocess on `CancelledError` — only `TimeoutError` triggers kill+reap; an external cancel (server shutdown / client disconnect) mid-`communicate()` leaves the process running [src/dev_helper_mcp/git/runner.py:_exec]
- [x] [Review][Patch] `proc.kill()` unguarded in the exit race — `kill()` on an already-exited process can raise `ProcessLookupError`, replacing `GitTimeout` with a raw error [src/dev_helper_mcp/git/runner.py:_exec]
- [x] [Review][Patch] Missing/unrunnable git binary leaks a raw `FileNotFoundError` — violates the module contract ("only `GitTimeout`/`NotAGitRepo` raised here"); map spawn failure to typed `Internal` [src/dev_helper_mcp/git/runner.py:_exec]
- [x] [Review][Patch] Add regression tests — acquire-timeout permit-no-leak, repo-lock path normalization, missing-git→`Internal` [tests/test_git_runner.py, tests/test_concurrency.py]
- [x] [Review][Defer] Store multi-statement atomicity — per-method `commit()` means `create_task`'s `add_task` + N×`add_worktree` is not one transaction; raw `IntegrityError` escapes core. Story 1.3 must add an explicit transaction boundary (rows written last, single tx) and map `IntegrityError` → typed `ActiveTaskConflict`/`BranchExists` [src/dev_helper_mcp/store.py] — deferred to Story 1.3 (create_task orchestration; out of 1.2 substrate scope)
- [x] [Review][Defer] Process-group kill for git children — `start_new_session` + `killpg` so hooks/children spawned by `worktree add` are reaped on mutation timeout [src/dev_helper_mcp/git/runner.py:_exec] — deferred, hardening (mutation timeout is 120s, rarely hit)
- [x] [Review][Defer] HOME-unset / relative `XDG_STATE_HOME` — `state_dir()` can resolve to a `~`/relative path in daemon/container contexts; absolutize + fallback [src/dev_helper_mcp/config.py:state_dir] — deferred, rare env (HOME set under normal `uv tool install` use)
- [x] [Review][Defer] RepoLockRegistry unbounded growth — locks minted lazily, never evicted; slow leak over many repos in a long-lived process [src/dev_helper_mcp/git/repo_lock.py] — deferred, negligible at v1 single-user scale

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
This story builds the **safe substrate** that every later tool sits on. Build ONLY: `errors.py`, `git/runner.py` (`run_git` + two pools + `NotAGitRepo` preflight), the per-repo mutex, `core/slug.py`, `store.py` (bootstrap + schema + version-check), and config constants. **Explicitly OUT of scope** (do not pull forward):
- **No MCP tool** — `create_task`/`list_worktrees`/`remove_worktree`/`update_task`/`list_tasks` are Stories 1.3–1.6. `tools/`, `tools/models.py`, `tools/handlers.py`, and the `{ok,data,error}` **envelope conversion** are NOT built here (the envelope is a Story 1.3 adapter concern; `errors.py` only *defines* the taxonomy).
- **No porcelain parsing** — `git/porcelain.py` (parse `worktree list --porcelain -z`) is Story 1.5 / Epic 2. `run_git` returns raw bytes; nothing parses them yet.
- **No projection / cache / dashboard** — `projection.py`, `cache.py`, `dashboard/` are Epic 2.
- **No task CRUD / conflict / rollback logic** — one-active-per-slug (1.3), UPSERT on re-task (1.3), cross-repo rollback (1.4). Store delivers schema + bootstrap only; do not implement `create_task` orchestration.
- **No lockfile / single-instance** — `lock.py` is Story 3.1. The per-repo mutex here is the *in-process* concurrency primitive, a different thing from the machine-global lockfile.

The deliverable: a proven off-loop git path (two pools, timeout-safe), an atomic persistence layer (two tables, WAL, version-guarded), the slug gate, the full typed error taxonomy, and the per-repo serialization primitive — each unit-testable with no server running.

### Binding invariants this story establishes (architecture.md §Invariants)
- **Invariant 1 — exactly one `run_git()`** and its correct pool; never `subprocess`/`os.system` for git anywhere else. [Source: architecture.md#Invariants; #Structure & Process Patterns]
- **Invariant 4 — derive-on-read / never persist derived state:** the two tables store ONLY task records + per-repo `(repo_path, branch, worktree_path)` links — never worktree existence (git porcelain is the sole truth, consumed in Epic 2). Do not add a "worktree exists" column. [Source: architecture.md#Invariants; #Data Architecture]
- **Invariant 6 — no blocking call on the event loop:** all git via `run_git`; all DB via `aiosqlite`. No `subprocess.run`, no synchronous `sqlite3`. [Source: architecture.md#Invariants]
- **Invariant 7 — SDK-isolation seam:** `errors.py`, `git/runner.py`, `git/repo_lock.py`, `core/slug.py`, `store.py` import **no** `mcp`/`starlette`. `tests/test_adapter_seam.py` already scans `core/`, `git/`, and `store.py` (in `SEAM_MODULES`) — adding such an import is an automatic gate failure. [Source: architecture.md#Invariants; tests/test_adapter_seam.py]
- **Invariant 11 — `now_iso()` only** for timestamps (already in `util.py`); when 1.3 writes `created_at`/`updated_at` it uses it. The schema columns are `TEXT` ISO-8601 `Z`. [Source: architecture.md#Invariants; src/dev_helper_mcp/util.py]
- **Invariant 12 — per-`repo_path` async mutex** serializes same-repo mutations; read/refresh ops don't take it; the global lockfile (3.1) is a different layer. [Source: architecture.md#Invariants; #Per-repo mutation mutex]

### Critical gotchas (carry into implementation)
- **asyncio objects + `asyncio.run()` per test (HIGH RISK — proven in Story 1.1's no-`pytest-asyncio` setup).** Story 1.1 drives async tests with `asyncio.run()` inside sync test functions, and each `asyncio.run()` spins up a **fresh event loop**. An `asyncio.Semaphore` / `asyncio.Lock` created at *import time* binds to (or caches state from) whatever loop first touches it and will misbehave or error ("bound to a different event loop") across tests. **Mitigation:** construct the two semaphores and the per-repo lock registry *inside* the running loop — wrap them in a `GitRunner`/holder object created at app startup (`server_factory`/`server` in the adapter layer instantiates it) and in each test's `asyncio.run(...)` body. Do not stash live semaphores/locks as module globals. [Source: 1-1 story Debug Log — "No pytest-asyncio"; #Testing rules]
- **`communicate()` is the drain.** Use `await asyncio.wait_for(proc.communicate(), timeout)` — it reads both pipes to EOF, avoiding the pipe-buffer deadlock that hand-rolled `proc.stdout.read()` + `proc.wait()` can hit. On timeout: `proc.kill()` then `await proc.wait()` **before** raising, so no zombie. [Source: architecture.md#Authentication & Security — async-git execution; #Invariants]
- **WAL needs a file DB in tests.** `:memory:` ignores/can't meaningfully assert `journal_mode=WAL`. Use a `tmp_path` file DB for the WAL assertion; `:memory:` is fine for pure schema/cascade logic. [Source: architecture.md#Structure & Process Patterns — "temp/:memory: DB for Store tests"]
- **`PRAGMA` ordering with aiosqlite.** Set `journal_mode`, `busy_timeout`, `foreign_keys` on the connection at bootstrap; `foreign_keys=ON` is **per-connection** in SQLite — set it on every connection the Store opens, not once globally, or the cascade silently won't fire.
- **`run_git` returns non-zero, doesn't raise it.** Only `GitTimeout` and `NotAGitRepo` are raised by the git layer. `BranchExists`/`WorktreePathInUse`/`BaseRefNotFound`/`DirtyWorktree`/`UnmergedBranch` are classified by *callers* (1.3–1.5) from the returned `returncode`/`stderr` — don't bake that mapping into `run_git`.

### Conventions to follow (architecture.md §Implementation Patterns; project-context.md)
- `snake_case` functions/vars/modules; `PascalCase` classes; `UPPER_SNAKE` module constants; type hints on every public signature; `_`-prefixed privates; module names are nouns. [Source: architecture.md#Naming Patterns; project-context.md#Naming & structure]
- All tunables in `config.py` — no magic numbers in modules. [Source: architecture.md#File Organization Patterns]
- Core raises typed `DevHelperError` (never ad-hoc error dicts). The envelope conversion is the adapter's job (1.3+) — not this story. [Source: architecture.md#Result Envelope & Error Patterns]
- `logging.getLogger(__name__)` per module; level from `DEV_HELPER_LOG` (default `INFO`); to stderr; never log full annotation contents at `INFO`. [Source: project-context.md#Naming & structure]
- DB keys / status literals lowercase `snake_case`; status set is exactly `running`/`blocked`/`review`/`done` (the CHECK constraint). [Source: architecture.md#Naming Patterns; #Data Architecture]

### Source tree components to touch (this story only)
Creates (NEW):
```
src/dev_helper_mcp/
├── errors.py             # DevHelperError base + full per-code subclasses + .as_dict()
├── store.py              # aiosqlite Store: WAL/busy_timeout/foreign_keys bootstrap, user_version check, two-table schema
├── core/
│   └── slug.py           # slugify() + InvalidTaskName rules (max 60, reject empty/reserved/./..)
└── git/
    ├── runner.py         # run_git(): create_subprocess_exec, read vs mutation pool, timeout→kill+reap, pinned env, -C, NotAGitRepo preflight
    └── repo_lock.py      # per-repo_path asyncio.Lock registry (mutation mutex)
tests/
├── test_errors.py
├── test_slug.py
├── test_git_runner.py
├── test_store.py
└── test_concurrency.py
```
Modifies (UPDATE):
```
src/dev_helper_mcp/config.py   # + git pool/timeout consts, GIT_ENV, slug rules, state-dir/db-path resolver, SCHEMA_VERSION
tests/conftest.py              # + tmp_git_repo fixture (reused by 1.3–1.5); existing ASGI fixtures untouched
```
Modules **deferred** (do NOT create): `tools/`, `dashboard/`, `lock.py`, `projection.py`, `cache.py`, `git/porcelain.py`, `core/worktrees.py`, `core/tasks.py`. [Source: architecture.md#Complete Project Directory Structure]

### Files being modified — current state to preserve
- **`src/dev_helper_mcp/config.py`** today holds only port/host/origin constants (`APP_NAME`, `MCP_PATH`, `DEFAULT_PORT`, `PORT_RANGE`, `BIND_HOST`, `ALLOWED_ORIGIN_HOSTS`). **Append** the new substrate constants; do not remove or rename the existing ones — `server.py`, `middleware.py`, `server_factory.py` import them. [Source: src/dev_helper_mcp/config.py]
- **`src/dev_helper_mcp/util.py`** already provides `now_iso()` — reuse it; do not add a second timestamp helper. [Source: src/dev_helper_mcp/util.py]
- **`src/dev_helper_mcp/core/__init__.py`** and **`git/__init__.py`** are seam-anchor docstrings only — `core/slug.py`, `git/runner.py`, `git/repo_lock.py` land beside them without touching the anchors. [Source: src/dev_helper_mcp/core/__init__.py; git/__init__.py]
- **`tests/conftest.py`** holds the in-process `httpx.ASGITransport` client factory — leave it intact; only **add** the `tmp_git_repo` fixture. [Source: 1-1 story File List]
- **`tests/test_adapter_seam.py`** already lists `store.py` in `SEAM_MODULES` and scans `core/`+`git/` recursively — your new modules are auto-policed for `mcp`/`starlette` imports the moment they exist; no edit needed, but keep them SDK-free. [Source: tests/test_adapter_seam.py]

### Previous story (1.1) intelligence — applies directly here
- **Python is 3.14, not 3.12.** `project-context.md` (operator decision, 2026-06-22) overrides the architecture doc's "target 3.12": `.python-version`=3.14, `requires-python>=3.14`, ruff `target-version=py314`. Use 3.14 stdlib freely (e.g. `asyncio.timeout` context manager is available; `asyncio.wait_for` also fine). Don't "restore" 3.12. [Source: project-context.md#Technology Stack; 1-1 Change Log]
- **No `pytest-asyncio`** — keep dev deps to `ruff`/`pytest`/`httpx`. Drive async with `asyncio.run()`. (See the asyncio-objects gotcha above.) [Source: 1-1 Debug Log; project-context.md#Testing rules]
- **`aiosqlite` 0.22.1 is already pinned** (added in 1.1, unused until now) — no new dependency needed; just import it. The minimal-dependency posture holds — add **no** new runtime or dev deps in this story. [Source: 1-1 Dev Notes; project-context.md#Technology Stack]
- **Ruff line-length 100, scoped to `src`/`tests`** (`extend-exclude` covers `_bmad*`/`docs`/`.claude`). Run the gate locally: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. [Source: project-context.md#Code-quality gate]
- **`{ok, data, error}` envelope was seeded by `ping` in 1.1** but the typed `DevHelperError`→envelope path is *this story's `errors.py` (definition) + Story 1.3's handler (conversion)*. Don't build the conversion yet. [Source: 1-1 Dev Notes#Conventions]
- **Two 1.1 review items are deferred, not yours:** the TOCTOU port race → Story 3.1; `Mount("/", …)` shadowing → Epic 2. Neither touches the substrate. [Source: deferred-work.md]

### Git / recent-work intelligence
Recent commits are all BMad planning + the Story 1.1 skeleton (`9e33106 1-1 review finished`). The only `src/` code is the adapter skeleton (server/middleware/factory/cli) + `util.now_iso()` + empty `core`/`git` packages. No prior git-execution or persistence code exists to match — follow the architecture conventions and the gotchas above. Baseline commit for this story: `9e33106`. [Source: git log; 1-1 File List]

### Story sizing note (from implementation-readiness report m1)
1.2 is the **largest** story (run_git + two pools, two-table Store, slug, full error taxonomy, per-repo mutex) — a flagged candidate for a 1.2a/1.2b split (git-execution vs persistence). The pieces are cohesive ("safe substrate") and each AC is crisp, so it is being delivered **whole**. If it proves too big in one pass, the natural seam is Tasks 1–5 (git/slug/errors/mutex) vs Task 6 (Store). [Source: implementation-readiness-report-2026-06-22.md#m1]

### Latest tech / version notes
- **`mcp` is not touched here** — pure core-layer story; the SDK seam stays clean. [Source: project-context.md#SDK-isolation seam]
- **`aiosqlite` 0.22.1** — async wrapper over stdlib `sqlite3`; `PRAGMA`s run as `await db.execute("PRAGMA ...")`; `foreign_keys` is per-connection. WAL is the bootstrap default per architecture (polling dashboard reads while tools write → avoids `SQLITE_BUSY`). [Source: project-context.md#Technology Stack; architecture.md#Data Architecture]
- **Python 3.14 asyncio** — `create_subprocess_exec`, `wait_for`, `Semaphore`, `Lock` all stdlib; `proc.communicate()` drains pipes. No third-party async libs. [Source: project-context.md#Async & git discipline]

### Project Structure Notes
- **`git/repo_lock.py` is a deliberate addition** not explicitly named in the architecture's directory tree (which pins `git/runner.py` + `git/porcelain.py`). The per-repo mutex needs a home; placing it in `git/` (beside the mutation pool it guards, core-layer, SDK-free) is the lowest-surprise choice and lets `core/tasks.py` (1.3) import it. If the team prefers, fold the registry into `git/runner.py` — but a separate noun-named module matches the "one concept per module" rule. Per `project-context.md`, pattern changes belong in `architecture.md` first; this is a small, in-spirit addition flagged here for the reviewer.
- Runtime state lives at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db` — **never** under `src/` or in the repo. `config.default_db_path()` resolves it; tests override with `tmp_path`. [Source: architecture.md#File Organization Patterns; project-context.md#Persistence]
- No other conflicts with the unified structure — this story is a strict subset of the architecture's defined core layer.

### Testing standards
- `tests/` mirrors `src/`; files `test_<module>.py`. [Source: architecture.md#Structure & Process Patterns]
- Prefer temp/`:memory:` DB for Store tests (file DB for the WAL assertion); a real `tmp_git_repo` for `run_git` tests; no port needed for any test in this story. [Source: architecture.md#Structure & Process Patterns; #Test harness baseline]
- Drive async tests with `asyncio.run()` — no `pytest-asyncio`. [Source: project-context.md#Testing rules]
- Everything runs under the enforced pre-commit gate (fast suite, `-m "not slow"`); add **no** `slow`-marked test in this story. [Source: project-context.md#Code-quality gate; 1-1 Dev Notes#Testing standards]

### References
- [Source: epics.md#Story 1.2: Async git execution and persistence substrate] — acceptance criteria
- [Source: epics.md#AR-14 / implementation-readiness §AR mapping] — per-repo mutex home is Story 1.2
- [Source: architecture.md#Invariants] — invariants 1, 4, 6, 7, 11, 12
- [Source: architecture.md#Data Architecture] — two-table schema, WAL/busy_timeout/foreign_keys, version-check migrations
- [Source: architecture.md#Authentication & Security] — two-pool async-git (3s read sem2 / ~120s mutation sem4), kill+reap+drain, pinned env, slug rules
- [Source: architecture.md#API & Communication Patterns] — error taxonomy (stable codes), per-repo mutation mutex
- [Source: architecture.md#Result Envelope & Error Patterns] — DevHelperError → envelope (conversion deferred to 1.3)
- [Source: architecture.md#Complete Project Directory Structure] — module homes (`errors.py`, `store.py`, `git/runner.py`, `core/slug.py`) + test files
- [Source: architecture.md#Structure & Process Patterns] — single run_git, async discipline, test harness baseline
- [Source: project-context.md] — Python 3.14, SDK seam, async/git discipline, persistence/derive-on-read, testing gotchas, code-quality gate
- [Source: 1-1-runnable-secure-global-mcp-server-skeleton.md] — seam anchors, no-pytest-asyncio pattern, aiosqlite pinned, config.py current contents
- [Source: deferred-work.md] — 1.1 deferrals (TOCTOU port race → 3.1; Mount shadowing → Epic 2), neither in substrate scope
- [Source: implementation-readiness-report-2026-06-22.md#m1] — 1.2 sizing flag (deliver whole; split seam if needed)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- **Deterministic timeout test without a hanging git command.** `git` has no
  built-in sleep, so the command-timeout path is forced with an injected tiny
  `read_timeout=0.001`: `asyncio.wait_for(proc.communicate(), 0.001)` reliably
  raises `TimeoutError` before even a fast `rev-parse HEAD` completes →
  `kill()`+`wait()`+`GitTimeout`. The test then asserts `_read_sem._value`
  returned to full, proving the `finally` released the slot (no leak/deadlock).
- **Acquire fail-fast tested by occupying the slot, not by slowness.** With
  `read_pool_size=1`, the test manually `acquire()`s the single read slot, then
  asserts `run_git(..., pool=READ)` raises `GitTimeout` within
  `read_acquire_timeout=0.05` (fail fast rather than queue). White-box use of
  the private semaphore is acceptable in a same-package unit test.
- **`GitRunner` pools are instance-scoped, not module globals.** Confirmed the
  no-`pytest-asyncio` pattern (fresh loop per `asyncio.run()`) works because each
  test constructs its own `GitRunner`/`RepoLockRegistry` inside the loop — no
  asyncio primitive crosses loops.
- **WAL needs a file DB.** `PRAGMA journal_mode=WAL` returns `memory` on
  `:memory:`; the WAL assertion uses a `tmp_path` file DB. FK-cascade test uses a
  file DB too and proves `foreign_keys=ON` is live (cascade only fires when on).
- **`require_git_repo` preflight (not in-`run_git` classification).** Chose an
  explicit `rev-parse --is-inside-work-tree` preflight raising `NotAGitRepo` —
  this is what `create_task` (1.3/1.4) will call; `run_git` itself stays generic
  and returns non-zero exits for callers to classify.

### Completion Notes List

- Ultimate substrate delivered whole (not split) per operator decision. All 5
  ACs satisfied; full suite **69 passed** (68 fast + 1 pre-existing `slow`
  smoke), enforced pre-commit gate green (exit 0).
- **AC 1** — single `GitRunner.run_git(repo, args, *, pool=READ|MUTATION)` via
  `create_subprocess_exec` (never shell), `-C <repo>`, pinned `GIT_ENV`. Read
  pool 3s/sem2/2s-acquire; mutation pool 120s/sem4. Tunables in `config.py`,
  injectable for tests. ✅ `test_git_runner`.
- **AC 2** — timeout → `kill()`+`wait()` (reaped) + drained via `communicate()`
  → `GitTimeout`; non-repo path → `NotAGitRepo`. ✅ `test_git_runner`.
- **AC 3** — `core/slug.py` `slugify()`: lowercase/hyphenate/collapse/strip,
  max 60 (reject, no truncate), reject empty/reserved/`.`/`..` → `InvalidTaskName`.
  ✅ `test_slug`.
- **AC 4** — `Store.open()`: two tables (PKs + `ON DELETE CASCADE`),
  `foreign_keys=ON`/WAL/`busy_timeout` per connection, XDG `state.db`,
  `user_version` refuse-newer (raises typed `Internal`). ✅ `test_store`.
- **AC 5** — `RepoLockRegistry.lock_for(repo_path)` async mutex serializes
  same-repo work, different repos run concurrently, read ops never take it. ✅
  `test_concurrency`.
- **Full error taxonomy** (15 codes) defined in `errors.py` with `.as_dict()`;
  only `GitTimeout`/`NotAGitRepo`/`InvalidTaskName`/`Internal` exercised now —
  the rest are the substrate for 1.3–1.6/3.x. ✅ `test_errors`.
- Adapter-seam test (now scanning `store.py` + `git/` + `core/`) stays green —
  no `mcp`/`starlette` leaked into the new core modules. No new dependencies
  added (`aiosqlite` was pre-pinned in 1.1). Out-of-scope items (tools/envelope,
  porcelain, projection/cache, task CRUD, lockfile) deliberately not built.

### File List

- `src/dev_helper_mcp/errors.py` (added) — DevHelperError + full taxonomy + as_dict()
- `src/dev_helper_mcp/config.py` (modified) — git pool/timeout consts, GIT_ENV, slug rules, state-dir/db-path resolver, SCHEMA_VERSION
- `src/dev_helper_mcp/core/slug.py` (added) — slugify() + InvalidTaskName rules
- `src/dev_helper_mcp/git/runner.py` (added) — GitRunner: run_git, two pools, timeout→kill+reap, require_git_repo
- `src/dev_helper_mcp/git/repo_lock.py` (added) — RepoLockRegistry per-repo async mutex
- `src/dev_helper_mcp/store.py` (added) — aiosqlite Store: WAL/foreign_keys/busy_timeout bootstrap, two-table schema, user_version check
- `tests/conftest.py` (modified) — added tmp_git_repo fixture; existing ASGI fixtures untouched
- `tests/test_errors.py` (added)
- `tests/test_slug.py` (added)
- `tests/test_git_runner.py` (added)
- `tests/test_concurrency.py` (added)
- `tests/test_store.py` (added)

## Change Log

- 2026-06-22 — Implemented the async-git + persistence substrate: full typed
  error taxonomy (`errors.py`), single `run_git()` with two permit pools +
  timeout/kill-reap + `NotAGitRepo` preflight (`git/runner.py`), per-repo async
  mutation mutex (`git/repo_lock.py`), slug validation (`core/slug.py`), and the
  two-table WAL/`foreign_keys`/version-checked `Store` (`store.py`); plus
  `config.py` tunables and 5 test files (+`tmp_git_repo` fixture). Delivered
  whole per operator decision (no 1.2a/1.2b split). Full suite 69 passed, gate
  green. Status: ready-for-dev → in-progress → review.
- 2026-06-22 — **Code review** (Blind Hunter + Edge Case Hunter + Acceptance
  Auditor): all 5 ACs confirmed. Applied 5 patches — repo-lock key normalization
  (`os.path.abspath`) so path aliases share one mutex; `run_git` now kills+reaps
  the subprocess on `CancelledError`; `proc.kill()` guarded against
  `ProcessLookupError`; missing git binary → typed `Internal` (contract honored);
  +3 regression tests. 1 decision resolved (keep `Internal` for the version-check
  refusal — startup-only, never hits a tool envelope). 4 items deferred (store
  multi-statement atomicity → Story 1.3; process-group kill; HOME/XDG edge;
  lock-registry eviction). 8 dismissed as noise. Full suite **72 passed**, gate
  green. Status: review → done.
