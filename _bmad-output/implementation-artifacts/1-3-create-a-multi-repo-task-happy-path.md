---
baseline_commit: bad5fa9bdc2d3e2867cd09e408ea210059fe8582
---

# Story 1.3: Create a multi-repo task (happy path)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent,
I want to create a task spanning one or more repositories in a single `create_task` call,
so that each repo gets its own isolated worktree + `agent/<task>` branch for my unit of work.

## Acceptance Criteria

1. **Single-repo create (FR-1; AR-13).**
   **Given** one valid repo path `A` and a task name,
   **When** I call `create_task(task_name, description, repos=[A])`,
   **Then** a worktree is created at `<A>.worktrees/<task>/` on branch `agent/<task>` from `A`'s HEAD, a `task` row (status `running`) and one `task_worktree` row are persisted, and it returns `{ok:true, data:{task_id, status, worktrees:[{repo_path, worktree_path, branch}]}}` — snake_case keys, `now_iso()` timestamps in the DB.

2. **Multi-repo create is all-or-nothing, rows written last in one transaction (AR-13).**
   **Given** several valid repos `[A,B,C]` that all succeed,
   **When** I call `create_task`,
   **Then** one worktree + `agent/<task>` branch is created per repo and one `task_worktree` row per repo is committed in a **single SQLite transaction** (the `task` row + all `task_worktree` rows written **last**, after every worktree succeeds — never a partial commit).

3. **Optional `base_ref` applies to every repo.**
   **Given** an optional `base_ref`,
   **When** provided,
   **Then** each repo's worktree/branch is created from that ref; the ref **must exist in every requested repo** (else `BaseRefNotFound`, checked in preflight). When omitted, each repo branches from its own `HEAD`.

4. **Active-slug reuse is rejected; `done` is the only reusable state (FR-6; Party Mode 2026-06-22 — highest-risk seam).**
   **Given** an existing task already using the same `<task>` slug,
   **When** `create_task` is called,
   **Then** `ActiveTaskConflict` is returned and **nothing is created** — where **"active" is defined as `status != 'done'`**, so `running`, `blocked`, **and `review`** all conflict.
   Regression tests (both required): `create → set status review → create same slug` **rejects** with `ActiveTaskConflict`; `create → set status done → create same slug` **succeeds** (re-task / UPSERT).
   **The predicate MUST be `status != 'done'`, NOT an enumerated allowlist** — an allowlist that lists `running`/`blocked` and forgets `review` is the exact bug this AC exists to prevent.

5. **Preflight collision detection — before any repo is mutated.**
   **Given** the `agent/<task>` branch or the target worktree directory already exists in **any** requested repo,
   **When** `create_task` is called,
   **Then** `BranchExists` / `WorktreePathInUse` is returned **before any repo is mutated** (no branch, no worktree, no DB row created in any repo). `NotAGitRepo` (non-repo path) and `InvalidTaskName` (bad slug) are likewise rejected in preflight.

## Tasks / Subtasks

> **Build order** (each step independently testable; tests build up): config path/branch helpers → `core/tasks.py` preflight → provisioning → `store.py` single-transaction persist (+ retask UPSERT) → `tools/models.py` + `tools/handlers.py` → wire into `server_factory.py` lifespan → tests.
> **Scope fence:** happy path + preflight rejection only. **No** post-preflight rollback/compensation, **no** `RollbackIncomplete`, **no** `RepoMutator` fault-injection seam, **no** cache/projection/dashboard — see **Scope boundaries**.

- [x] **Task 1 — config: worktree-path + branch-name conventions (AC: 1, 3, 5)**
  - [x] Add to `src/dev_helper_mcp/config.py` (append; do not touch existing consts): `BRANCH_PREFIX = "agent/"` and `WORKTREE_DIR_SUFFIX = ".worktrees"`. No magic strings for these in any module (project-context: all tunables in `config.py`).
  - [x] Add a pure path helper (in `config.py` or `core/tasks.py`): `worktree_path_for(repo: Path, slug: str) -> Path` returning `repo.parent / f"{repo.name}{WORKTREE_DIR_SUFFIX}" / slug` — i.e. `<repo>.worktrees/<slug>` as a **sibling** of the repo. Branch name helper: `f"{BRANCH_PREFIX}{slug}"` → `agent/<slug>`.

- [x] **Task 2 — `core/tasks.py`: the `create()` orchestrator — preflight (AC: 3, 4, 5)**
  - [x] Create `src/dev_helper_mcp/core/tasks.py` (core layer — **no `mcp`/`starlette` import**; it `from ..git.runner import GitRunner, Pool`, `from ..git.repo_lock import RepoLockRegistry`, `from ..core.slug import slugify`, `from ..store import Store`, `from ..errors import ...`, `from ..util import now_iso`). `core/` importing `git/` and `store` is allowed — all SDK-free.
  - [x] Signature: `async def create(task_name: str, description: str, repos: list[str], *, base_ref: str | None = None, runner: GitRunner, locks: RepoLockRegistry, store: Store) -> dict`. Dependencies are **injected** (DI) — core never constructs the runner/store (testability + the "asyncio objects live in the running loop" rule). Returns the plain success `data` dict; raises typed `DevHelperError` on any guard (the adapter converts).
  - [x] **Slug first:** `slug = slugify(task_name)` (raises `InvalidTaskName`). `branch = f"{BRANCH_PREFIX}{slug}"`.
  - [x] **Normalize + dedup + canonical-order the repo set:** map each repo through `os.path.abspath`; dedup; require `len(repos) >= 1`. **Acquire per-repo mutexes in sorted-abspath order** (`for repo in sorted(abspaths): await locks.lock_for(repo).acquire()`), release in reverse in a `finally`. **Sorted order is mandatory — it prevents the A=[x,y]/B=[y,x] lock-ordering deadlock.** (`RepoLockRegistry.lock_for` already keys on `os.path.abspath`.)
  - [x] **Active-slug conflict gate (AC 4) — do this in preflight, before any mutation:** `existing = await store.get_task(slug)`; if `existing is not None and existing["status"] != "done"` → raise `ActiveTaskConflict` (details: `{task_id, status}`). Predicate is literally `!= "done"` — never an allowlist.
  - [x] **Per-repo preflight (AC 5) — loop all repos, raise on the FIRST collision, mutate nothing:** for each repo (a) `await runner.require_git_repo(repo)` (raises `NotAGitRepo`); (b) branch-exists check → `BranchExists`; (c) worktree-dir-exists check (`os.path.exists(worktree_path_for(repo, slug))`) → `WorktreePathInUse`; (d) if `base_ref` given, ref-exists check → `BaseRefNotFound`. Put the offending `repo`/`branch`/`base_ref` in `error.details`.
  - [x] Git checks use **`runner.run_git(repo, [...], pool=Pool.READ)` and classify by `GitResult.returncode`** — `run_git` **returns** non-zero (it does NOT raise for ordinary git failures; only `GitTimeout`/`NotAGitRepo`/`Internal` are raised). Branch-exists: `["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]` → `returncode == 0` means exists. Base-ref-exists: `["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"]` → `returncode != 0` means not found.

- [x] **Task 3 — `core/tasks.py`: provisioning (mutation pool) (AC: 1, 2, 3)**
  - [x] After preflight passes, for each repo (canonical order) create branch + worktree from `base_ref or "HEAD"` via the **mutation** pool. Recommended single atomic command (preflight already guaranteed branch+path are free): `await runner.run_git(repo, ["worktree", "add", "-b", branch, str(wt_path), base or "HEAD", "--"], pool=Pool.MUTATION)` — creates the `agent/<task>` branch **and** its worktree in one git call. (The architecture's two-step `branch` + `worktree add` is equivalent and also acceptable; the `-b` form is preferred for atomicity and a cleaner future rollback seam.) Treat a non-zero `returncode` here as a hard failure (see scope note below) — surface the git `stderr` in an `Internal`/typed error; **full reverse-order compensation is Story 1.4**, not this story.
  - [x] Use `str(wt_path)` (absolute) for the worktree path and pass `--` to end git option parsing (defense-in-depth; the slug is already validated). Collect `(repo_path, worktree_path, branch)` per repo for the response + persistence.

- [x] **Task 4 — `store.py`: single-transaction persist + retask UPSERT (AC: 2, 4) — resolves the 1.2 deferral**
  - [x] Add ONE atomic persistence method, e.g. `async def persist_created_task(self, *, task_id, description, status, created_at, updated_at, worktrees: list[tuple[str,str,str]]) -> None`, that writes the `task` row + all `task_worktree` rows in a **single transaction with no intermediate `commit()`** (the existing `add_task`/`add_worktree` each commit individually — do **not** reuse them for create; keep them for back-compat/tests or refactor them to share the new path). Rows are written **last**, after provisioning succeeds (AC 2).
  - [x] **Retask of a `done` slug (AC 4 success path):** the `task_id` PK may already exist (status `done`). Within the same transaction, **clear the old record first** — `DELETE FROM task WHERE task_id = ?` cascades (`ON DELETE CASCADE`) and removes stale `task_worktree` rows — then INSERT the fresh `task` + `task_worktree` rows. **Preserve the original `created_at`** (read it before deleting, or via `existing["created_at"]` already fetched in preflight) and set `updated_at = now`; on a brand-new slug, `created_at == updated_at == now`. (Equivalent `INSERT … ON CONFLICT(task_id) DO UPDATE` is acceptable, but you must still purge old `task_worktree` rows.)
  - [x] **Map `aiosqlite.IntegrityError` → typed error** (the 1.2 review deferral): a `task` PK clash that slips past the preflight gate (TOCTOU race) → `ActiveTaskConflict`; a `task_worktree` PK clash → treat as an internal invariant break. Never let a raw `IntegrityError` escape `store.py` into the envelope.
  - [x] `foreign_keys=ON` is already set per-connection at bootstrap — the cascade only fires because of it; do not remove it.

- [x] **Task 5 — `tools/models.py` + `tools/handlers.py`: the adapter (AC: 1, 4, 5)**
  - [x] Create `src/dev_helper_mcp/tools/__init__.py`, `tools/models.py`, `tools/handlers.py` (**adapter layer — these MAY import `mcp`/pydantic**; they are NOT scanned by `test_adapter_seam.py`, which polices only `core/`, `git/`, `store.py`, `projection.py`, `cache.py`).
  - [x] `tools/models.py`: `class CreateTaskIn(BaseModel)` with `task_name: str`, `description: str`, `repos: list[str]` (≥1), `base_ref: str | None = None`. Pydantic `*In` models live **only** here, at the boundary — `core.tasks.create` takes plain args, never the model.
  - [x] `tools/handlers.py`: `async def create_task(inp: CreateTaskIn, *, deps) -> dict` that calls `core.tasks.create(inp.task_name, inp.description, inp.repos, base_ref=inp.base_ref, runner=deps.runner, locks=deps.locks, store=deps.store)` and wraps: `return {"ok": True, "data": result, "error": None}`; `except DevHelperError as e: return {"ok": False, "data": None, "error": e.as_dict()}`; `except Exception: return {"ok": False, "data": None, "error": Internal("unexpected error").as_dict()}` — **never leak a stack trace**. Match the `ping` envelope shape exactly (`{"ok", "data", "error"}`, all three keys).

- [x] **Task 6 — wire `create_task` into the adapter & lifespan (AC: 1)**
  - [x] In `server_factory.py` (adapter layer): construct the shared dependencies **inside the running loop** — extend the existing `lifespan` in `create_app` to build `GitRunner()` + `RepoLockRegistry()` and `await Store.open()` (default machine-global DB), expose them via a small holder (e.g. a `ToolDeps` dataclass) the `create_task` tool closure reads, and `await store.close()` on shutdown. **This is the trickiest wiring** — the FastMCP `ping` tool is registered in `build_mcp()` at build time, but the deps must be created at lifespan time (asyncio objects + the open DB connection belong to the serving loop). A mutable holder populated by the lifespan and captured by the tool closure is the lowest-surprise pattern; keep the existing `async with mcp_app.router.lifespan_context(mcp_app):` wrapping intact (load-bearing — Invariant 8).
  - [x] Register the tool with `@mcp.tool()` (verb-first `snake_case` name `create_task`) alongside `ping`. Keep `ping` working.

- [x] **Task 7 — tests (`tests/` mirrors `src/`; `test_<module>.py`) (AC: 1–5)**
  - [x] `tests/test_tasks.py` (primary — unit-test `core.tasks.create` directly with injected `GitRunner()`, `RepoLockRegistry()`, a tmp-file `Store`, and the `tmp_git_repo` fixture; drive with `asyncio.run()`, **no pytest-asyncio**):
    - single-repo success → branch `agent/<slug>` exists, worktree dir exists, 1 `task` row (status `running`) + 1 `task_worktree` row, returned `data` shape/keys exact (snake_case).
    - multi-repo success (build 2–3 `tmp_git_repo`s) → one branch+worktree+row per repo; assert all rows present (the single-transaction commit).
    - `base_ref` honored (create a second commit/branch in the fixture, pass it, assert the worktree HEAD matches).
    - **AC 4 regression (both):** seed a task then set its status to `review` via `store` → second create same slug raises `ActiveTaskConflict`; set status to `done` → second create same slug **succeeds** and re-tasks (created_at preserved, updated_at advanced). Also assert `blocked` conflicts.
    - **AC 5 preflight, no mutation:** pre-create the `agent/<slug>` branch in one repo → `BranchExists`, and assert **no** worktree/row created in **any** repo; pre-create the worktree dir → `WorktreePathInUse`; pass a non-repo dir → `NotAGitRepo`; pass a bad name → `InvalidTaskName`; pass a missing `base_ref` → `BaseRefNotFound`.
  - [x] `tests/test_store.py` (extend): the new single-transaction persist writes task + N worktree rows atomically; retask of a `done` slug replaces the record and purges old `task_worktree` rows while preserving `created_at`; a duplicate-active `task_id` insert maps to `ActiveTaskConflict` (not raw `IntegrityError`).
  - [x] `tests/test_handlers.py` (or fold into `test_tasks.py`): the `create_task` handler returns the `{ok, data, error}` envelope on success and `{ok:false, error:{code,...}}` on a typed error (e.g. `ActiveTaskConflict`), with all three envelope keys present.
  - [x] (Optional, fast) one in-process ASGI round-trip via `asgi_client_factory` proving the tool is registered and reachable — only if it stays in the fast suite (no real port, no `slow` marker).
  - [x] All tests pass under the enforced gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`.

### Review Findings

_Code review 2026-06-22 (3 layers: Blind Hunter, Edge Case Hunter, Acceptance Auditor). All 5 ACs and binding invariants verified SATISFIED — including the highest-risk AC-4 `status != 'done'` predicate. Findings below are quality/robustness, not AC failures._

- [x] [Review][Patch] Pre-commit gate's pytest phase is commented out — disables the only regression gate (no CI in v1) [.githooks/pre-commit:19-20] — FIXED: uncommented the `pytest -m "not slow"` phase
- [x] [Review][Patch] Worktree-path preflight uses `os.path.exists` (follows symlinks) — a broken symlink at the target slips through, then `worktree add` fails post-preflight as generic `Internal` instead of `WorktreePathInUse`; use `os.path.lexists` [core/tasks.py:94] — FIXED: switched to `os.path.lexists`
- [x] [Review][Patch] `create_task` closure dereferences `holder.deps` set to `None` on shutdown — a late/in-flight request during teardown hits `AttributeError` swallowed as opaque `Internal`; add a `holder.deps is None` not-ready guard [server_factory.py:86,119] — FIXED: added a `deps is None` → `Internal("server not ready")` envelope guard
- [x] [Review][Patch] `persist_created_task` only catches `IntegrityError` — other DB errors (e.g. `OperationalError` disk-full/locked) escape with the transaction open, leaving the connection unusable; and the `else` blanket-maps every `IntegrityError` to `ActiveTaskConflict` (a CHECK/NOT-NULL violation would be mis-reported). Rollback-and-reraise-as-`Internal` on non-mapped errors; tighten the conflict mapping [store.py:140-170] — FIXED: added an `aiosqlite.Error` rollback+`Internal` handler and narrowed the `ActiveTaskConflict` mapping to `task.task_id` PK clashes only
- [x] [Review][Defer] Post-preflight partial state — a mid-loop `worktree add` failure on repo N orphans repos 1..N-1's worktrees+branches with no compensation, and surfaces collisions as generic `Internal` not typed errors [core/tasks.py:116-133] — deferred, explicitly Story 1.4 scope (rollback / `RollbackIncomplete` / `RepoMutator`)
- [x] [Review][Defer] Per-repo mutex is process-local — two server processes wouldn't serialize, and same-slug/disjoint-repo concurrent creates can both provision before the persist-time `ActiveTaskConflict`, orphaning the loser's worktrees [core/tasks.py:63-70] — deferred, mitigated by the machine-global single-instance lock (Story 3.1) + rollback (Story 1.4)

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
This is the **happy path + preflight rejection**. The clean seam vs Story 1.4: every error in *this* story's ACs (`InvalidTaskName`, `NotAGitRepo`, `BranchExists`, `WorktreePathInUse`, `BaseRefNotFound`, `ActiveTaskConflict`) is raised **in preflight, before any git mutation** — so the success path and the rejection paths both leave the system clean with **no compensation needed**. Explicitly **OUT of scope** (do not pull forward):
- **Post-preflight rollback / compensation** — undoing worktrees+branches when a mutation fails *after* preflight passed (a race, disk error, etc.) is **Story 1.4**, together with `RollbackIncomplete` and the `RepoMutator` fault-injection seam. In 1.3, a post-preflight git failure may surface as a typed `Internal`/git error and leave residue — that is acceptable for this story; 1.4 hardens it. Do **not** build the reverse-order teardown matrix here.
- **`cache.py` / `projection.py` / dashboard** — Epic 2. **Do NOT call any `cache.refresh()`** in `create_task` (the module does not exist). The architecture's "mutation critical-section ordering" (read porcelain → project → swap cache) is the *full-system* picture; 1.3 builds only the git-mutation + persistence half. The response is built directly from the `(repo_path, worktree_path, branch)` tuples you just created, not from a cache.
- **`list_worktrees` / `remove_worktree`** — Story 1.5. **`update_task` / `list_tasks`** — Story 1.6. In 1.3 the AC-4 `review`/`done` regression tests set status by writing it **directly via the `Store`** (a test helper), because `update_task` does not exist yet.
- **`git/porcelain.py`** (parse `worktree list --porcelain -z`) — Story 1.5 / Epic 2. **`lock.py` (machine-global lockfile)** — Story 3.1 (the per-repo mutex here is a different, in-process layer).
- **Incremental add-worktree to an existing task** — the repo set is fixed at create (future feature, FR-1 commentary).

### What the substrate already gives you (verified shipped in 1.1/1.2 — reconcile against this, NOT the architecture's pseudo-code)
The architecture doc sketches helper signatures that **differ from the shipped code**. Use the **real** APIs below — they are the source of truth:
- **`git/runner.py` → `class GitRunner`**, method `async run_git(repo: str | Path, args: Sequence[str], *, pool: Pool) -> GitResult` where `Pool.READ` / `Pool.MUTATION` and `GitResult(returncode: int, stdout: bytes, stderr: bytes)`. **Non-zero exits are RETURNED, not raised** — callers classify (`BranchExists` etc.) from `returncode`/`stderr`. Only `GitTimeout`, `NotAGitRepo`, `Internal` are raised. Also `async require_git_repo(repo)` → raises `NotAGitRepo` (use it for the AC-5 repo check). `stdout` is **bytes** — compare against `b"..."` or `.decode()`. [Source: src/dev_helper_mcp/git/runner.py]
- **`git/repo_lock.py` → `class RepoLockRegistry`**, `lock_for(repo_path: str) -> asyncio.Lock` (keys on `os.path.abspath`). One registry per running loop. [Source: src/dev_helper_mcp/git/repo_lock.py]
- **`core/slug.py` → `slugify(name: str) -> str`** (raises `InvalidTaskName`). The returned slug is **both** `task_id` **and** the `agent/<task>` branch suffix. [Source: src/dev_helper_mcp/core/slug.py]
- **`store.py` → `class Store`**: `await Store.open(db_path=None)`; existing methods `add_task(task_id, description, status, created_at, updated_at)`, `add_worktree(task_id, repo_path, branch, worktree_path)`, `get_task(task_id) -> dict | None` (keys `task_id, description, status, created_at, updated_at`), `count_worktrees(task_id)`, `delete_task(task_id)`, `close()`. **`add_task`/`add_worktree` each `commit()` individually — they are NOT a transaction.** Task 4 adds the single-transaction method. Tables: `task(task_id PK, description, status CHECK IN ('running','blocked','review','done'), created_at, updated_at)` + `task_worktree(task_id FK ON DELETE CASCADE, repo_path, branch, worktree_path, PK(task_id, repo_path))`. [Source: src/dev_helper_mcp/store.py]
- **`errors.py`**: full taxonomy already defined; `DevHelperError.as_dict() -> {"code","message","details"}`. You only **raise** them. Relevant: `InvalidTaskName, NotAGitRepo, BranchExists, WorktreePathInUse, BaseRefNotFound, ActiveTaskConflict, GitTimeout, Internal`. [Source: src/dev_helper_mcp/errors.py]
- **`util.py` → `now_iso()`** UTC ISO-8601 `Z`, second precision — the ONLY timestamp source for `created_at`/`updated_at`. [Source: src/dev_helper_mcp/util.py]
- **`config.py`**: pools/timeouts/slug rules/DB-path already there; **append** `BRANCH_PREFIX`, `WORKTREE_DIR_SUFFIX`. Do not rename/remove existing consts (`server.py`/`middleware.py`/`server_factory.py` import them). `RESERVED_SLUGS` is `{"", ".", ".."}` — do **not** add `main`/`master` (the `agent/` prefix namespaces the branch; 1.2 deliberately omitted them). [Source: src/dev_helper_mcp/config.py]
- **`server_factory.py`**: `build_mcp()` registers `ping` via `@mcp.tool()`; `create_app(port)` builds the parent Starlette app with an app-owned `lifespan` that wraps `mcp_app.router.lifespan_context` (load-bearing — keep it). This is the adapter layer (imports `mcp`/`starlette`) — your `tools/handlers.py` and deps wiring go here / beside it. [Source: src/dev_helper_mcp/server_factory.py]

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 1 — exactly one git path:** every git call via `GitRunner.run_git` with the correct pool. **Read** pool for the 4 preflight checks (`require_git_repo`, branch-exists, base-ref-exists); **mutation** pool for `worktree add -b`. Never `subprocess`/`os.system` for git in `src/`. [Source: architecture.md#Invariants; project-context.md#Async & git discipline]
- **Invariant 4 — derive-on-read / never persist derived state:** store ONLY the task record + `(repo_path, branch, worktree_path)` links. **No "worktree exists" column**, no porcelain parsing, no cache write. [Source: architecture.md#Data Architecture; project-context.md#Persistence]
- **Invariant 6 — no blocking call on the event loop:** git via `run_git`, DB via `aiosqlite`. `os.path.exists`/`os.path.abspath` are fine (cheap, pure-ish). No `subprocess.run`, no sync `sqlite3`. [Source: architecture.md#Invariants]
- **Invariant 7 — SDK seam:** `core/tasks.py` and the `store.py` changes import **no** `mcp`/`starlette` (auto-policed by `tests/test_adapter_seam.py`, which scans `core/`+`git/`+`store.py`). `tools/` IS allowed the SDK and is NOT scanned. [Source: tests/test_adapter_seam.py; project-context.md#SDK-isolation seam]
- **Invariant 11 — `now_iso()` only** for `created_at`/`updated_at`. [Source: project-context.md#Data, format & error contract]
- **Invariant 12 — per-`repo_path` async mutex** serializes same-repo mutations; acquire in **sorted-abspath order** (deadlock-safe) across the repo set, release in reverse; read/refresh ops don't take it. [Source: architecture.md#Per-repo mutation mutex; src/dev_helper_mcp/git/repo_lock.py]
- **Error contract:** core raises typed `DevHelperError`; the adapter (`tools/handlers.py`) converts to `{ok:false, error:{code,message,details}}`. Unexpected exception → `{ok:false, error:{code:"Internal"}}`, never a stack trace. **All JSON keys snake_case.** [Source: project-context.md#Data, format & error contract]

### Critical gotchas (carry into implementation)
- **`run_git` returns non-zero; it does not raise it.** Classify preflight results by `GitResult.returncode` — do NOT wrap read checks in try/except expecting a raised error (you'll only ever catch `GitTimeout`). [Source: 1-2 Dev Notes; src/dev_helper_mcp/git/runner.py]
- **Lock-ordering deadlock is real for multi-repo.** Two concurrent creates with overlapping repos in opposite list order deadlock unless every caller acquires in the **same canonical (sorted-abspath) order**. This is the #1 correctness trap in this story. [Source: architecture.md#Per-repo mutation mutex]
- **asyncio objects + `asyncio.run()` per test.** Construct `GitRunner`/`RepoLockRegistry`/`Store` **inside** the running loop (the lifespan in prod; each `asyncio.run()` body in tests) — never as import-time globals, or they bind to a dead loop. The deps holder is populated by the lifespan for exactly this reason. [Source: 1-2 Dev Notes; src/dev_helper_mcp/git/runner.py docstring]
- **Single transaction = no intermediate commit.** Reusing the shipped `add_task`+`add_worktree` would commit the task row before the worktree rows — violating AC 2. Add the dedicated atomic method. [Source: deferred-work.md#story-1-2 — Store multi-statement atomicity]
- **`IntegrityError` must not escape `store.py`.** Map it to `ActiveTaskConflict` (the TOCTOU safety net behind the preflight gate). [Source: deferred-work.md#story-1-2]
- **Worktree path is a sibling of the repo:** `<repo>.worktrees/<slug>`, i.e. `repo.parent / f"{repo.name}.worktrees" / slug` — NOT inside the repo, NOT under `src/`. [Source: project-context.md#Persistence; architecture.md#worktree path convention]
- **`done` is the only reusable state** and the predicate is `!= "done"` — `review` MUST conflict. Party Mode 2026-06-22 flagged the allowlist-omits-`review` bug as the highest-risk seam of the 4-status change. [Source: epics.md#Story 1.3 AC; project-context.md]

### Previous-story (1.1 / 1.2) intelligence that applies directly
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`, `src/` layout. Use 3.14 stdlib freely. [Source: project-context.md#Technology Stack; 1-1]
- **No `pytest-asyncio`** — drive async tests with `asyncio.run()` in sync test funcs. Dev deps stay `ruff`/`pytest`/`httpx`; **add no new dependency** (`aiosqlite`, `mcp`, pydantic-via-mcp all already present). [Source: project-context.md#Testing; 1-2]
- **`tmp_git_repo` fixture** already in `tests/conftest.py` (real repo, one commit on branch `main`) — reuse it; instantiate 2–3 for multi-repo tests. The in-process `asgi_client_factory` is there for the optional tool round-trip. Leave existing fixtures intact. [Source: tests/conftest.py]
- **Enforced pre-commit gate** runs `ruff check` + `ruff format --check` + `pytest -m "not slow"` via `.githooks/pre-commit`; add **no** `slow` test in this story. [Source: project-context.md#Code-quality gate; 1-1]
- **Two 1.1 deferrals are not yours** (TOCTOU port race → 3.1; `Mount("/", …)` route-shadowing → Epic 2). [Source: deferred-work.md]
- **1.2 explicitly deferred to THIS story:** the single-transaction boundary + `IntegrityError → ActiveTaskConflict/BranchExists` mapping (Task 4). That is the one substrate gap 1.3 must close. [Source: deferred-work.md#story-1-2]

### Git / recent-work intelligence
HEAD is `bad5fa9 1-2`. The substrate (`errors.py`, `store.py`, `core/slug.py`, `git/runner.py`, `git/repo_lock.py`, `config.py` consts, `util.now_iso`, the adapter skeleton + `ping`) is in place and reviewed. No `tools/`, `core/tasks.py`, `core/worktrees.py`, `cache.py`, `projection.py`, or `git/porcelain.py` exist yet — 1.3 introduces `tools/` and `core/tasks.py` only. Baseline commit for this story: `bad5fa9`. [Source: git log; src tree]

### Latest tech / version notes
- **`git worktree add -b <branch> <path> <start-point>`** creates the branch and its worktree in one call and fails non-zero if `<branch>` exists or `<path>` is occupied — preflight already guards both, so the happy path won't hit those. Pass `--` before the path. Equivalent two-step (`git branch <b> <ref>` then `git worktree add <path> <b>`) is what the architecture sketches and is equally valid. [Source: architecture.md#Async-git execution]
- **`git rev-parse --verify --quiet <ref>`** → exit 0 + prints the OID if the ref resolves, non-zero/silent otherwise — the existence primitive for both the branch (`refs/heads/agent/<slug>`) and `base_ref` (`<ref>^{commit}`) checks. [Source: git rev-parse]
- **`mcp` 1.28.x FastMCP** — a tool returning a plain `dict` serializes as JSON **text** content (`content[0].text`), `structuredContent` stays `None`; if you write an ASGI round-trip test, assert by parsing the text. [Source: project-context.md#Testing rules — proven in 1.1]
- **`aiosqlite`** — `IntegrityError` is `aiosqlite.IntegrityError` (re-exported from stdlib `sqlite3`); transactions: run statements then a single `await conn.commit()`, or `async with conn.execute(...)`; FK cascade only fires with `foreign_keys=ON` (already set per connection). [Source: project-context.md#Technology Stack; src/dev_helper_mcp/store.py]

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/core/tasks.py` (orchestrator), `src/dev_helper_mcp/tools/__init__.py`, `tools/models.py`, `tools/handlers.py`. The architecture also names `core/worktrees.py` for per-repo create/list/remove; for 1.3 the per-repo "branch+worktree create" primitive may live inline in `core/tasks.py` (minimal) **or** seed `core/worktrees.py` for 1.5 to extend — either is in-spirit; flag the choice for the reviewer. Keep it SDK-free.
- **UPDATE:** `src/dev_helper_mcp/config.py` (+ `BRANCH_PREFIX`, `WORKTREE_DIR_SUFFIX`, optional path helper); `src/dev_helper_mcp/store.py` (+ single-transaction persist + retask + `IntegrityError` mapping); `src/dev_helper_mcp/server_factory.py` (register `create_task`, build/teardown deps in the lifespan); `tests/test_store.py` (+ persist/retask tests). NEW tests: `tests/test_tasks.py` (+ optionally `tests/test_handlers.py`).
- **DEFERRED, do NOT create:** `cache.py`, `projection.py`, `git/porcelain.py`, `lock.py`, the full `core/worktrees.py` list/remove surface. [Source: architecture.md#Complete Project Directory Structure; 1-2 scope]
- Runtime DB stays at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`; worktrees are repo siblings — never under `src/` or the package. [Source: project-context.md#Persistence]

### Testing standards
- `tests/` mirrors `src/`; `test_<module>.py`; drive async with `asyncio.run()`; reuse `tmp_git_repo`. Prefer **unit-testing `core.tasks.create` directly** (inject `GitRunner()`/`RepoLockRegistry()`/tmp-file `Store`) over going through the server — fast, deterministic, no port. Use a **file** DB (not `:memory:`) where you assert persistence across the transaction. No `slow`-marked test. Everything green under the enforced gate. [Source: architecture.md#Structure & Process Patterns; project-context.md#Testing; 1-2]

### References
- [Source: epics.md#Story 1.3: Create a multi-repo task (happy path)] — user story + all 5 ACs (verbatim), 4-status `review`-conflict amendment, scope boundaries
- [Source: epics.md#Epic 1] — epic goal, FR/AR coverage (FR-1, FR-6; AR-9, AR-11, AR-13, AR-14), story sequencing (1.4 rollback / 1.5 list-remove / 1.6 update-list are later)
- [Source: architecture.md#API & Communication Patterns — Async-git execution] — two pools, git command shapes, worktree path convention
- [Source: architecture.md#Data Architecture] — two-table schema, single-transaction create, UPSERT-on-retask, FK cascade
- [Source: architecture.md#Per-repo mutation mutex] — Invariant 12, deadlock-safe ordering rationale
- [Source: architecture.md#Error taxonomy] — `BranchExists`/`WorktreePathInUse`/`BaseRefNotFound`/`ActiveTaskConflict`/`NotAGitRepo`/`InvalidTaskName`
- [Source: architecture.md#Result Envelope & Error Patterns] — `{ok,data,error}`, `DevHelperError`→envelope conversion in the adapter
- [Source: architecture.md#Implementation Patterns & Consistency Rules] — `CreateTaskIn` at the boundary only; core takes plain args; handler try/except pattern
- [Source: deferred-work.md#story-1-2] — Store single-transaction + `IntegrityError → ActiveTaskConflict/BranchExists` (closed by Task 4)
- [Source: src/dev_helper_mcp/git/runner.py; git/repo_lock.py; core/slug.py; store.py; errors.py; util.py; config.py; server_factory.py] — actual shipped substrate APIs (authoritative over the doc's pseudo-code)
- [Source: tests/conftest.py; tests/test_adapter_seam.py] — `tmp_git_repo` + ASGI fixtures; the seam scope (`core/`+`git/`+`store.py`; `tools/` exempt)
- [Source: project-context.md] — Python 3.14, SDK seam, async/git discipline, snake_case + `now_iso()`, persistence/derive-on-read, testing gotchas, quality gate
- [Source: 1-2-async-git-execution-and-persistence-substrate.md] — substrate Dev Notes, gotchas, the 1.2→1.3 deferral

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Opus 4.8, 1M context)

### Debug Log References

- Full gate green: `uv run ruff check .` ✓, `uv run ruff format --check .` ✓, `uv run pytest` ✓ (92 passed, incl. the slow uvicorn smoke test; 91 in the fast `-m "not slow"` suite).
- Adapter-seam test (`tests/test_adapter_seam.py`) passes with the new `core/tasks.py` — no `mcp`/`starlette` import leaked into the core layer.

### Completion Notes List

- **Task 1 (config):** appended `BRANCH_PREFIX = "agent/"`, `WORKTREE_DIR_SUFFIX = ".worktrees"`, and pure helpers `worktree_path_for(repo, slug)` (sibling `<repo>.worktrees/<slug>`) + `branch_name_for(slug)`. No existing consts touched.
- **Tasks 2–3 (`core/tasks.py`):** new SDK-free orchestrator `create(...)` with injected deps. Slug-first; normalize→dedup→**sorted-abspath** repo set; per-repo mutexes acquired in sorted order and released in reverse (`finally`) — deadlock-safe (Invariant 12). Active-slug gate uses the literal `status != "done"` predicate (never an allowlist — `review`/`blocked` conflict). Per-repo preflight (`require_git_repo` → `NotAGitRepo`; `rev-parse --verify --quiet refs/heads/<branch>` → `BranchExists`; `os.path.exists(wt_path)` → `WorktreePathInUse`; optional `<base_ref>^{commit}` → `BaseRefNotFound`) raises on the first collision before any mutation. Provisioning: single atomic `git worktree add -b <branch> <path> <start> --` on the MUTATION pool; non-zero exit → typed `Internal` (full reverse-order compensation is deferred to Story 1.4 per scope fence).
- **Task 4 (`store.py`):** added `persist_created_task(...)` — task row + all `task_worktree` rows in ONE transaction (single `commit()`, rows written last). Retask DELETE is scoped to `status='done'` (cascade purges stale worktree rows; `created_at` preserved by the caller), which is precisely what lets a TOCTOU-surviving **active** row collide on the PK INSERT → `aiosqlite.IntegrityError` mapped to `ActiveTaskConflict`; a `task_worktree` PK clash maps to `Internal`. `IntegrityError` never escapes `store.py` (closes the 1.2 deferral). Rollback on error keeps the connection usable.
- **Task 5 (`tools/`):** new `tools/__init__.py`, `tools/models.py` (`CreateTaskIn` — pydantic boundary only), `tools/handlers.py` (`ToolDeps` dataclass + `create_task` handler wrapping the core result in the exact `{ok, data, error}` envelope; typed errors → `{ok:false,error:…}`, unexpected → `Internal`, never a stack trace).
- **Task 6 (wiring):** `server_factory.build_mcp(holder)` now also registers an async `create_task` tool whose closure reads a mutable `_DepsHolder`; the `create_app` lifespan builds `GitRunner()`/`RepoLockRegistry()`/`await Store.open()` inside the serving loop, populates the holder, and `await store.close()` on shutdown. The load-bearing `mcp_app.router.lifespan_context` wrapping (Invariant 8) is preserved. `ping` still works.
- **Task 7 (tests):** `tests/test_tasks.py` (primary, unit-tests `core.tasks.create` across all 5 ACs incl. both AC-4 regressions and full AC-5 preflight-no-mutation matrix), `tests/test_store.py` (+3 persist/retask/IntegrityError tests), `tests/test_handlers.py` (envelope on success + typed errors), and an in-process ASGI round-trip in `tests/test_server_factory.py` proving the tool is registered and reachable (fast suite, no port). Added an autouse `_isolate_state_dir` fixture in `conftest.py` so the lifespan's default `Store.open()` never touches the real `~/.local/state` DB.
- **Reviewer flag (per Project Structure Notes):** the per-repo "branch+worktree create" primitive lives **inline in `core/tasks.py`** for this story (minimal) rather than seeding `core/worktrees.py` — Story 1.5 can extract it when it adds list/remove. Choice surfaced here as requested.

### File List

- `src/dev_helper_mcp/config.py` (modified — added `BRANCH_PREFIX`, `WORKTREE_DIR_SUFFIX`, `worktree_path_for`, `branch_name_for`)
- `src/dev_helper_mcp/store.py` (modified — added `persist_created_task` single-transaction persist + retask + `IntegrityError` mapping; imported `ActiveTaskConflict`)
- `src/dev_helper_mcp/core/tasks.py` (new — `create()` orchestrator: preflight + provisioning)
- `src/dev_helper_mcp/tools/__init__.py` (new — adapter package marker)
- `src/dev_helper_mcp/tools/models.py` (new — `CreateTaskIn`)
- `src/dev_helper_mcp/tools/handlers.py` (new — `ToolDeps`, `create_task` handler)
- `src/dev_helper_mcp/server_factory.py` (modified — register `create_task`, `_DepsHolder`, build/teardown deps in the lifespan)
- `tests/conftest.py` (modified — autouse `_isolate_state_dir` fixture)
- `tests/test_tasks.py` (new — core orchestrator tests, AC 1–5)
- `tests/test_store.py` (modified — persist/retask/IntegrityError tests)
- `tests/test_handlers.py` (new — envelope tests)
- `tests/test_server_factory.py` (modified — in-process `create_task` round-trip)

## Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-06-22 | 0.1.0 | Implemented Story 1.3 — `create_task` multi-repo happy path + preflight rejection. New `core/tasks.py` orchestrator, `tools/` adapter, single-transaction persist + retask in `store.py`, lifespan-built tool deps. All 5 ACs satisfied; 92 tests pass under the enforced gate. |
