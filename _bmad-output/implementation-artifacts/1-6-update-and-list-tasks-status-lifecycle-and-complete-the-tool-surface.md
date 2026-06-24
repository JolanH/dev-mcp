---
baseline_commit: 3c41a67
---

# Story 1.6: Update and list tasks (status lifecycle) and complete the tool surface

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent,
I want to update a task's status and description and query tasks by status or repo,
so that I can self-report progress and a monitoring view can reflect it.

## Acceptance Criteria

_Verbatim from epics.md#Story 1.6 (lines 294–325). BDD Given/When/Then._

1. **`update_task` — status/description update + `updated_at` bump + four-state guard.**
   **Given** an existing task,
   **When** I call `update_task(task_id, status?, description?)`,
   **Then** the status and/or description are updated and `updated_at` is bumped;
   **And** a status outside the four-state set {`running`,`blocked`,`review`,`done`} is rejected (`blocked`=awaiting input, `review`=awaiting review).

2. **Status transition matrix — `done` is terminal.**
   **Given** the status transition graph,
   **When** `update_task` changes status,
   **Then** transitions among `running` ↔ `blocked` ↔ `review` are all legal, and **`done` is terminal** — a task in `done` cannot be moved back to an active status (re-activating a slug is done via a new `create_task`, not `update_task`); the full 4×4 transition matrix is enforced (legal set passes, illegal `done → *` rejects), asserted by a parametrized table test.

3. **`TaskNotFound` on missing task.**
   **Given** a non-existent `task_id`,
   **When** `update_task` is called,
   **Then** `TaskNotFound` is returned.

4. **`done` releases the slug + flags closed.**
   **Given** a task updated to `done`,
   **When** it completes,
   **Then** it no longer counts as the active task for its `<task>` slug (the slug becomes reusable) and is flagged closed (moves to the folded Done section on the dashboard).

5. **`list_tasks` — filterable, full model fields + per-repo links.**
   **Given** existing tasks,
   **When** I call `list_tasks(status?, repo?)`,
   **Then** the matching tasks are returned with all model fields including their per-repo `(repo_path, branch, worktree_path)` links.

6. **Exactly 5 tools advertised — tool surface complete.**
   **Given** a connected MCP client,
   **When** it enumerates the tool surface,
   **Then** exactly **5** tools are advertised with input/output schemas — `create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks` — all returning the `{ok,data,error}` envelope with snake_case keys.

## Tasks / Subtasks

> **Build order** (each step independently testable, red→green): config status/transition constants → `store.py` `update_task` + `list_tasks` queries → `core/tasks.py` `update_task` (four-state guard + transition matrix + `TaskNotFound`) → `core/tasks.py` `list_tasks` (store read + filters) → `tools/models.py` `UpdateTaskIn`/`ListTasksIn` → `tools/handlers.py` two adapters → `server_factory.py` two `@mcp.tool()` closures **+ remove the `ping` seed** + docstring → parametrized transition-matrix table test → AC6 "exactly-5-tools" enumeration test → gate green.

> **Scope fence (read first — anti-scope-creep):** this is the **final Epic 1 story** — it adds the two task tools (`update_task`, `list_tasks`), **removes the throwaway `ping` seed**, and thereby pins the **exactly-5** tool surface. **NO** `cache.py`/`projection.py`/`/state`/dashboard/`CacheSnapshot`/`TaskView` (all Epic 2 — `list_tasks` reads the **Store**, NOT live git, NOT a cache; see Dev Notes). **NO** live-git fan-out in `list_tasks` (that is `list_worktrees`, already shipped in 1.5, and the Epic 2 projection). **NO** `add_worktree`/incremental repo attach (deferred project-wide, AR-13). **NO** changes to `create_task`/rollback/`GitRepoMutator`/`remove_worktree` semantics. **NO** schema migration (the `task` table already has every needed column incl. the status `CHECK`). **NO** new error class beyond the single `InvalidStatus` addition below (see Decision A — and confirm with the operator first; default is to add it).

- [x] **Task 1 — Status + transition constants in `config.py` (AC: 1, 2)**
  - [x] Add to `src/dev_helper_mcp/config.py` (no magic strings scattered in modules — project-context.md#Naming & structure): `TASK_STATUSES: tuple[str, ...] = ("running", "blocked", "review", "done")`, `ACTIVE_STATUSES = frozenset({"running", "blocked", "review"})`, `TERMINAL_STATUS = "done"`. These mirror the existing SQL `CHECK (status IN ('running','blocked','review','done'))` in `store.py:25` — keep the two in sync (the CHECK is the DB backstop; the constant is the core-layer guard so a bad status is rejected as typed-error-as-data **before** hitting the DB CHECK).
  - [x] Define the transition rule once (a small pure predicate, in `core/tasks.py` or `config.py`): **`legal_transition(src, dst) := dst in TASK_STATUSES and src != TERMINAL_STATUS`** — i.e. from any **active** state (`running`/`blocked`/`review`) any of the four states is reachable (including active→`done` and idempotent self-transitions); **from `done`, nothing is** (`done` is terminal — `done → *` always illegal, including `done → done`). This yields the 4×4 matrix: 12 legal (3 active source rows × 4) / 4 illegal (the `done` source row). [Source: epics.md:307-309]

- [x] **Task 2 — `store.py`: `update_task` + `list_tasks` queries (AC: 1, 3, 5)**
  - [x] **`async def update_task(self, task_id: str, *, status: str | None = None, description: str | None = None, updated_at: str) -> bool`** — a parameterized `UPDATE task SET … WHERE task_id = ?`. Build the `SET` clause dynamically from the provided fields (only `status`/`description` that are non-`None`) **plus always `updated_at = ?`**; **never touch `created_at`** (preserve it — architecture.md:336-337). Parameterized placeholders only (no string interpolation of values). `commit()`. **Return whether a row matched** (`cursor.rowcount == 1`) so core can raise `TaskNotFound` when `False` (alternative: core does `get_task` first — see Task 3; pick one, do not do both). Do **not** map the DB `CHECK` violation to `Internal` silently — core validates status first so the CHECK is never the rejection path (defensive only).
  - [x] **`async def list_tasks(self, *, status: str | None = None, repo: str | None = None) -> list[dict]`** — returns one dict per matching **task** with all model fields and its per-repo links nested:
    `{task_id, description, status, created_at, updated_at, worktrees: [{repo_path, branch, worktree_path}, …]}`.
    Implementation: SELECT from `task` (filtered by `status` when provided), then attach each task's `task_worktree` rows (filtered/limited to `repo` when provided — a `repo` filter returns tasks that **touch** that repo, with that task's links). Either a join + group-in-Python, or a `task` SELECT followed by a `list_worktree_links`-style read. **Reuse the existing `list_worktree_links` (store.py:195) join shape where practical.** Sort tasks by `task_id` ASC, worktrees by `repo_path` ASC (stable order — mirrors the projection contract). Parameterized queries only; read-only; **no schema change**.
  - [x] Insert both methods near the existing task methods (after `delete_task`, store.py:247-249). **Reuse `get_task` (store.py:183), `count_worktrees`, `delete_task` as-is.**
  - [x] **Tests** (`tests/test_store.py`): tmp-file/`:memory:` `Store`. (a) `update_task` changes status+description and bumps `updated_at` while `created_at` is unchanged; updating only `status` leaves `description`; returns `False`/no-match for an unknown `task_id`. (b) `list_tasks` returns full task rows with nested links; `status=` and `repo=` filters narrow correctly; empty store → `[]`. No git, no safety surface (pure Store unit tests).

- [x] **Task 3 — `core/tasks.py`: `update_task` (four-state guard + transition matrix + not-found) (AC: 1, 2, 3, 4)**
  - [x] **`async def update_task(task_id: str, *, status: str | None = None, description: str | None = None, store: Store) -> dict`** (SDK-free core; plain args; injected `store`; raises typed `DevHelperError`). Lives in `core/tasks.py` (architecture.md:728 names `tasks.py` as the home for "create/update/list"). **No `runner`/`locks`** — `update_task` is a pure DB mutation, not a per-repo git op, so it takes **no per-repo mutex** (the mutex is keyed by `repo_path`; this touches the `task` row, not a worktree). [Invariant 12 / AR-14: only per-repo git mutations take the mutex.]
  - [x] Order of operations:
    1. **Resolve** the existing task: `existing = await store.get_task(task_id)`. If `None` → raise **`TaskNotFound`** (AC3).
    2. **Validate `status`** (when provided): if `status not in config.TASK_STATUSES` → raise the invalid-status typed error (AC1 — see **Decision A** for the code; default `InvalidStatus`).
    3. **Validate the transition** (when `status` provided and differs is irrelevant — guard the rule uniformly): if **not** `legal_transition(existing["status"], status)` → raise the illegal-transition typed error (AC2). Concretely: a task whose current status is `done` rejects **any** `status` update (`done` is terminal); active→{active,done} all pass. Put a `reason` in `details` (`"not_in_set"` vs `"illegal_transition"`) so the agent can branch.
    4. **Apply**: `ts = now_iso()`; `await store.update_task(task_id, status=status, description=description, updated_at=ts)`. (If you chose store-returns-bool for not-found instead of the `get_task` precheck, still do the `get_task` here because the transition matrix needs the **current** status — so the `get_task` precheck is the natural design; the store bool is then a redundant safety net, not the primary not-found path.)
    5. **Return** the updated task dict (re-read via `get_task`, or build from inputs + `existing`): `{task_id, status, description, created_at, updated_at}` (snake_case; `now_iso()` timestamp).
  - [x] **AC4 is a consequence, not extra code:** setting `status='done'` makes the task non-active. "Active" is defined **everywhere** as `status != 'done'` (see `core/tasks.py:86-89` — `create_task`'s ActiveTaskConflict gate uses exactly this predicate). So a `done` task automatically (a) stops counting as the active task for its slug → a later `create_task(same_slug)` succeeds (the create gate's `existing["status"] != "done"` is now `False`); and (b) is "flagged closed" purely by its `status` value (the dashboard folds `done` — Epic 2 / Story 2.4a renders it; **1.6 only sets the status, it does not delete the row or touch worktrees**). **Do NOT delete the task or its `task_worktree` links on `done`** — that is `remove_worktree`'s last-worktree path (Story 1.5), a *different* "closed" semantics (see Dev Notes "Two coexisting 'closed' semantics"). Add a regression test: `create → update_task(review) → create same slug` **rejects** (`ActiveTaskConflict`); `create → update_task(done) → create same slug` **succeeds** (this is the same highest-risk seam Story 1.3 pinned — epics.md:226).
  - [x] **Tests** (`tests/test_tasks.py`, extend): injected `Store` (tmp-file), `asyncio.run()` (no `pytest-asyncio`). (a) AC1: update status+description, assert `updated_at` advanced & `created_at` preserved & values persisted via `get_task`. (b) AC1: out-of-set status (e.g. `"merged"`) → the invalid-status typed error, nothing changed. (c) AC3: unknown `task_id` → `TaskNotFound`. (d) AC4: the two slug-reuse regressions above. **No git needed for update_task tests** (no worktree creation) — but the AC4 slug-reuse regressions DO call `create_task`, so those use `tmp_git_repo` (git-safety rule applies — see below).

- [x] **Task 4 — `core/tasks.py`: `list_tasks` (store read + filters) (AC: 5)**
  - [x] **`async def list_tasks(*, status: str | None = None, repo: str | None = None, store: Store) -> list[dict]`** — thin core wrapper over `store.list_tasks` (normalize a falsy/empty `repo` to `None` like 1.5's `list_worktrees` fix — deferred-work.md#story-1-5 / worktrees.py:61: an empty-string `repo` must mean "no filter", NOT `os.path.abspath("") == cwd`; abspath the repo filter when non-empty for consistency with how links store absolute `repo_path`). Returns the list of task dicts with nested per-repo links (AC5 "all model fields including their per-repo links"). **Store read only — no git, no cache** (see Dev Notes "list_tasks is a Store read, not Epic 2").
  - [x] No mutex (read path). Raises only what the store surfaces (none expected beyond `Internal` on a DB error, handled by the adapter).
  - [x] **Tests** (`tests/test_tasks.py`): create 1-2 tasks (multi-repo via `tmp_git_repo`s), `list_tasks()` → assert each task carries `task_id/description/status/created_at/updated_at` + a `worktrees` list with correct `repo_path/branch/worktree_path`; `status=` filter and `repo=` filter narrow correctly; empty/`""` repo == no filter; unknown status → `[]`.

- [x] **Task 5 — `tools/models.py`: `UpdateTaskIn` + `ListTasksIn` (AC: 1, 5)**
  - [x] **`class UpdateTaskIn(BaseModel)`**: `task_id: str`, `status: str | None = None`, `description: str | None = None`. **Keep `status` as `str | None`, NOT a `Literal`/enum** — so an out-of-set value is rejected as **typed-error-as-data** by core (`{ok:false, error:…}`) rather than a Pydantic `ValidationError` that escapes the `{ok,data,error}` envelope (the model is built inside the `server_factory` closure, **outside** the handler's try/except — a `Literal` there would surface a raw validation failure, not the contract envelope). (See Dev Notes "Why status is `str`, not `Literal`".)
  - [x] **`class ListTasksIn(BaseModel)`**: `status: str | None = None`, `repo: str | None = None` (mirrors `ListWorktreesIn`, models.py:22).
  - [x] Insert after `RemoveWorktreeIn` (models.py:41). Pydantic `*In` models live ONLY here (one per tool — Invariant / architecture.md:606-608).

- [x] **Task 6 — `tools/handlers.py`: `update_task` + `list_tasks` adapters (AC: 1, 3, 5, 6)**
  - [x] **`async def update_task(inp: UpdateTaskIn, *, deps: ToolDeps) -> dict`** — unpack to core args, `data = await tasks.update_task(inp.task_id, status=inp.status, description=inp.description, store=deps.store)`, wrap in `{ok, data, error}`; `except DevHelperError → exc.as_dict()`; `except Exception → Internal("unexpected error")` (no stack-trace leak). **Mirror `create_task`/`list_worktrees` handlers EXACTLY** (handlers.py:38-72).
  - [x] **`async def list_tasks(inp: ListTasksIn, *, deps: ToolDeps) -> dict`** — `data = await tasks.list_tasks(status=inp.status, repo=inp.repo, store=deps.store)`; same envelope/error pattern.
  - [x] Insert after `remove_worktree` (handlers.py:93). Note `update_task`/`list_tasks` need only `deps.store` (not `runner`/`locks`) — but keep the `deps: ToolDeps` signature uniform with the other handlers.
  - [x] **Tests** (`tests/test_handlers.py`, extend): direct handler calls with a `ToolDeps` (tmp `Store`); assert envelope shape, snake_case keys, success path, `TaskNotFound`-as-data for `update_task`, and the invalid-status error-as-data. (Mirror the existing `create_task`/`list_worktrees`/`remove_worktree` handler tests.)

- [x] **Task 7 — `server_factory.py`: register 2 tools, REMOVE `ping`, complete the 5-tool surface (AC: 6)**
  - [x] **Remove the `ping` seed tool** (server_factory.py:69-72) — it was the Story 1.1 throwaway (project-context.md:37: "Story 1.1 ships only a throwaway `ping`"). AC6 mandates **exactly 5** tools (`create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks`); `ping` is not one of them. Removing it is **part of "complete the tool surface"** — do not leave a 6th tool.
  - [x] Add two `@mcp.tool()` async closures (after the `remove_worktree` closure, ~server_factory.py:139), each mirroring the `list_worktrees` closure (server_factory.py:98-111): read `deps = holder.deps`; if `None` → `{"ok": False, "data": None, "error": Internal("server not ready").as_dict()}` (the lifespan startup/teardown-window guard); build the `*In` model; delegate to the handler. Signatures: `async def update_task(task_id: str, status: str | None = None, description: str | None = None) -> dict` and `async def list_tasks(status: str | None = None, repo: str | None = None) -> dict`. Give each a concise docstring (FastMCP advertises it as the tool description) — e.g. document the four-state set + `done`-terminal for `update_task`, and the filters + returned fields for `list_tasks`; both return the `{ok,data,error}` envelope.
  - [x] **Update `build_mcp`'s module/function docstring** (server_factory.py:59-66) to list the final **5** tools (and drop `ping`). Remove the now-dead `now_iso` import if `ping` was its only user in this file (it is — server_factory.py:72; **verify** nothing else in the file uses `now_iso` before deleting the import, or ruff `F401` fails the gate).
  - [x] **Fix the `ping` test** (`tests/test_server_factory.py:50-70` `test_handshake_and_ping_roundtrip`): `ping` no longer exists, so this test breaks. **Repurpose it** into the AC6 surface-enumeration test (Task 8) or replace the `call_tool("ping")` round-trip with a `call_tool` against one of the 5 real tools (e.g. a `list_tasks` round-trip on an empty store). The SDK **handshake** assertion it carries is still valuable — preserve that, drop only the `ping`-specific parts.

- [x] **Task 8 — Transition-matrix table test + AC6 exactly-5-tools test (AC: 2, 6)**
  - [x] **Parametrized 4×4 transition table test** (`tests/test_tasks.py`): enumerate all 16 `(src, dst)` pairs over {`running`,`blocked`,`review`,`done`}; for each, set a task to `src` (seed via store/`update_task` from `running`; for `src='done'` set it `done` first), then `update_task(..., status=dst)`; assert the **12** non-`done`-source pairs succeed and the **4** `done`-source pairs reject with the illegal-transition error. This is AC2's mandated parametrized table test (epics.md:309). Keep it pure-Store (no git) where possible — seeding `src` does not need a worktree.
  - [x] **AC6 "exactly 5 tools" test** (`tests/test_server_factory.py`): over the in-process ASGI handshake (existing harness), `session.list_tools()` → assert the tool-name set is **exactly** `{create_task, list_worktrees, remove_worktree, update_task, list_tasks}` (length 5, **`ping` absent**), and each advertises an input schema. Optionally round-trip `update_task`/`list_tasks` once to assert the `{ok,data,error}` envelope + snake_case over the wire. **Remember the FastMCP serialization gotcha** (project-context.md:76): a tool returning a plain `dict` comes back as **JSON text** (`content[0].text`), `structuredContent` is `None` — parse the text to assert the envelope. In-process base URL **must** be `http://127.0.0.1:<port>` and the test body must wrap `async with app.router.lifespan_context(app)` (else 421 / "Task group not initialized" — project-context.md:73-75).

- [x] **Task 9 — Gate green + seam compliance + git-safety (AC: all)**
  - [x] `core/tasks.py` (and any touched core/store) import no `mcp`/`starlette` — `tests/test_adapter_seam.py` stays green (it scans `core/`+`git/`).
  - [x] If **Decision A** lands (`InvalidStatus` added): update `tests/test_errors.py` (it enumerates the taxonomy) so the new code is covered; propagate the addition to `errors.py`, `architecture.md` (Step-4 taxonomy), and `project-context.md` (the error-code list) — **pattern change in architecture.md, then propagate** (project-context.md#Code-quality gate & workflow). See Decision A for the alternative that needs no taxonomy change.
  - [x] Every git op in tests targets `tmp_git_repo`/`tmp_path` only (HARD git-safety rule — `tests/test_git_safety.py` static scan + autouse `_guard_project_repo_untouched` both enforce it). Most 1.6 tests are pure-Store (no git); only the AC4 slug-reuse regressions and `list_tasks` multi-repo fixtures spawn git — those use `tmp_git_repo`.
  - [x] Full gate green: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. **No** new runtime dependency, **no** new `slow`-marked test, **no** schema migration.

### Review Findings (code-review 2026-06-24)

_Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor, Opus 4.8). Acceptance Auditor: **0 AC violations** — all 6 ACs + scope fence confirmed satisfied. 2 decision-needed resolved by operator → 1 patch + 1 dismissed; 1 standalone patch; 3 dismissed as noise._

- [x] [Review][Patch] Guard the no-op update — when both `status` and `description` are `None`, return the existing task unchanged with NO DB write and NO `updated_at` bump (operator decision on the "no-op bumps updated_at" finding) [core/tasks.py `update_task`]. Flagged by Blind (High) + Edge (Low). **Fixed**: no-op guard added after the existence check; regression `test_update_task_noop_returns_unchanged_without_bump`.
- [x] [Review][Patch] Consume `store.update_task`'s matched-bool to close the TOCTOU phantom-success window (raise `TaskNotFound` on `False`) — the store method was built to return the bool as a "redundant safety net" but core discards it [core/tasks.py `update_task`]. Flagged by Edge (Low) + Auditor. **Fixed**: core now raises `TaskNotFound` when the store reports no matched row; regression `test_update_task_phantom_success_guarded`.

_Dismissed: (1) `list_tasks` status filter left permissive — standard read-filter semantics, AC5 does not mandate validation (operator decision); (2) `params: list[str]` annotation — accurate today; (3) `details` `from`/`to` keys — spec only mandates `reason`, present._

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
1.6 is the **last Epic 1 story**. It adds `update_task` + `list_tasks`, removes the `ping` seed, and locks the **exactly-5** tool surface. The biggest traps:
- **`list_tasks` is a Store read, NOT Epic 2.** The architecture describes a `CacheSnapshot`/`TaskView` view built by `projection.py`/`cache.py` from a **live per-repo git fan-out** — **that is Epic 2 and those modules do not exist.** epics AC5 says only "tasks returned with all model fields including their per-repo `(repo_path, branch, worktree_path)` links" — i.e. the **DB `task` rows + `task_worktree` links**, not live git, not orphan-detection, not a cache. **Where the architecture's view pseudo-code contradicts this, project-context wins** (project-context.md#Usage Guidelines: "this file wins — it reflects working code"). So `list_tasks` = `SELECT` from the two tables, grouped by task. Live-git worktree existence is `list_worktrees`' job (shipped 1.5) and the Epic 2 projection — do **not** pull it forward into `list_tasks`.
- **`update_task` is UPDATE-only — never UPSERT, never create.** It mutates an **existing** row (`TaskNotFound` if absent). Re-activating a `done` slug is a **new `create_task`** (epics AC2/AC4 are explicit), which already has its own UPSERT-style DELETE-done-then-insert path (`store.persist_created_task`, store.py:115-181). Do **not** route `update_task` through that path.
- **No worktree/git work.** `update_task`/`list_tasks` never spawn git, never create/remove worktrees, never take the per-repo mutex. `update_task` does not touch `task_worktree`; `list_tasks` only reads it.
- **Schema is already complete.** The `task` table already has `created_at`, `updated_at`, and the status `CHECK` (store.py:22-28, landed 1.2). **No migration.**

### ⚠️ Decision A — error code for invalid status / illegal transition (CONFIRM WITH OPERATOR)
epics AC1 ("a status outside the four-state set is rejected") and AC2 ("illegal `done → *` rejects") both require a **typed** rejection (errors are data — every tool returns `{ok,data,error}`), but the **fixed error taxonomy has no matching code** (project-context.md:48 / architecture.md:466-473 list: `BranchExists, WorktreePathInUse, BaseRefNotFound, DirtyWorktree, UnmergedBranch, TaskNotFound, ActiveTaskConflict, LockedWorktree, InvalidTaskName, GitTimeout, InstanceConflict, NotAGitRepo, RollbackIncomplete, PortUnavailable, Internal` — **none fit "invalid status value / illegal transition"**).
- **Recommended default (encoded in the tasks above): add a single new code `InvalidStatus`** — Story 1.6 is the designated owner of the four-state lifecycle + transition matrix (epics.md:119), so introducing the code here is a sanctioned, in-scope pattern change. Cover **both** failures with it (`details.reason ∈ {"not_in_set", "illegal_transition"}` so the agent can still distinguish). Propagate: `errors.py` (new `DevHelperError` subclass, `code="InvalidStatus"`), `architecture.md` Step-4 taxonomy, `project-context.md` error-code list, `tests/test_errors.py`.
- **Alternative (no taxonomy change):** reuse `InvalidTaskName` for both (semantically loose — it currently means "the slug failed validation") with a discriminating `details.reason`. Lower blast radius (no contract change) but a weaker semantic fit.
- **This is the single open question for the operator (see end of run).** Pick one before implementing; the story is written assuming `InvalidStatus`.

### Why `status` is `str`, not `Literal`, in `UpdateTaskIn`
The `*In` model is constructed in the `server_factory` closure (server_factory.py, e.g. line 109 `inp = ListWorktreesIn(...)`) — **outside** the handler's try/except. A `Literal["running",…]` status would raise a Pydantic `ValidationError` at construction for an out-of-set value, which propagates to FastMCP as a protocol/validation failure — **not** the `{ok:false, error:…}` envelope AC1 requires. Keeping `status: str | None` and validating in **core** makes every rejection uniform typed-error-as-data. (Same reasoning is why `create_task`'s slug validation lives in core, not the model.)

### Two coexisting "closed" semantics — do not conflate
There are now **two** distinct ways a task stops being active, by design:
1. **`update_task(status='done')` (this story):** sets `status='done'`, **keeps** the `task` row and all `task_worktree` links. The task is "closed" by status; the dashboard **folds** it into `✓ N done` (Epic 2 / 2.4a). The slug becomes reusable (a new `create_task` can take it because the active-gate is `status != 'done'`). Worktrees on disk are untouched (the operator may still `remove_worktree` them).
2. **`remove_worktree` of the last worktree (Story 1.5):** **DELETEs** the `task` row entirely (cascade clears the final link). The task disappears from `list_tasks` and the board. (1-5 story Dev Notes "AC5 DECISION SETTLED".)
Both are correct; they answer different needs (report-done vs. clean-up-done). **1.6 must NOT delete on `done`, and must NOT set a status on remove** — keep the two paths separate, exactly as the create/remove blast radii were kept separate in 1.4/1.5.

### What the substrate already gives you (verified shipped 1.1–1.5 — reconcile against THIS, not the architecture pseudo-code)
- **`store.py`** (the ONLY DB opener; SDK-free). Reuse as-is: `get_task(task_id) -> dict|None` (store.py:183 — returns `{task_id, description, status, created_at, updated_at}`); `list_worktree_links(repo?, task_id?)` (store.py:195 — the LEFT-JOIN shape to mirror for `list_tasks`' link attach); `count_worktrees`, `delete_task`. **You ADD**: `update_task(task_id, *, status?, description?, updated_at)` (dynamic-SET UPDATE, preserve `created_at`) and `list_tasks(status?, repo?)` (task SELECT + nested links). Schema **unchanged**. Parameterized queries only. [Source: store.py:22-28 schema, 90-249 methods]
- **`store.persist_created_task`** (store.py:115-181) — the existing **DELETE-done-then-insert** UPSERT path used by `create_task` for slug reuse; maps `IntegrityError` on `task.task_id` → `ActiveTaskConflict`. **`update_task` does NOT use this** (it's UPDATE-only). Listed so you don't accidentally duplicate its logic.
- **`core/tasks.py`** — already hosts `create(...)` (tasks.py:44-203). The active-task predicate is **`status != 'done'`** (tasks.py:86-89) — reuse this exact definition for AC4 (do not invent an enumerated allowlist that omits `review`; that was the 1.3 high-risk seam). Module const `_RUNNING = "running"` (tasks.py:41). Add `update_task` + `list_tasks` here. `now_iso` already imported (tasks.py:39).
- **`core/worktrees.py`** — the style template for new core fns: keyword-only injected deps, raises typed `DevHelperError`, returns plain dicts; the empty-`repo`-means-no-filter fix (worktrees.py:61, deferred-work.md#story-1-5) to replicate in `list_tasks`. [Source: worktrees.py:45-112]
- **`tools/handlers.py`** — the envelope adapter to mirror EXACTLY: unpack `*In` → core args → `{ok, data, error}`; `except DevHelperError → exc.as_dict()`; `except Exception → Internal("unexpected error")` (logged, no stack-trace leak). `ToolDeps(runner, locks, store)` dataclass (handlers.py:24-35). [Source: handlers.py:38-93]
- **`tools/models.py`** — Pydantic `*In` models ONLY (one per tool): `CreateTaskIn`, `ListWorktreesIn`, `RemoveWorktreeIn` (models.py:13-41) are the pattern. [Source: models.py]
- **`server_factory.py`** — the `@mcp.tool()` closure pattern incl. the `holder.deps is None → "server not ready"` startup/teardown guard (server_factory.py:98-111); `build_mcp` currently registers **ping + create_task + list_worktrees + remove_worktree** (4) — 1.6 removes `ping`, adds 2 → **exactly 5**. [Source: server_factory.py:59-139]
- **`errors.py`** — `TaskNotFound` (code="TaskNotFound"), `InvalidTaskName`, `Internal` all defined as `DevHelperError` subclasses; base `as_dict() -> {code, message, details}` (errors.py:14-31). The only possible **new** class is `InvalidStatus` (Decision A). [Source: errors.py:14-94]
- **`config.py`** — all tunables here (no magic numbers in modules). Add `TASK_STATUSES`/`ACTIVE_STATUSES`/`TERMINAL_STATUS`. `now_iso()` is in **`util.py:6`** (NOT config.py) — `from ..util import now_iso`. [Source: config.py; util.py:6-14]

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 2 — `{ok, data, error}` on every tool.** Core raises typed `DevHelperError`; the adapter converts. Never an ad-hoc dict; never a stack trace. Unexpected → `Internal`. [architecture.md:65-66, 612-626]
- **Invariant 7 — SDK seam:** `core/tasks.py` imports no `mcp`/`starlette` (auto-policed by `tests/test_adapter_seam.py`). Pydantic `*In` models stay in `tools/models.py`; core takes plain args.
- **Invariant 11 — timestamps via `now_iso()` only** (UTC ISO-8601 `Z`, second precision). `update_task` bumps `updated_at = now_iso()`, **preserves `created_at`**. Never `datetime.now()`/epoch ints. [architecture.md:75, 602-603]
- **Invariant 12 / AR-14 — per-`repo_path` mutex is for per-repo git mutations only.** `update_task`/`list_tasks` are not repo-scoped git ops → **no mutex**. [architecture.md:444-450; epics.md:73]
- **All JSON keys snake_case** (`task_id`, `repo_path`, `worktree_path`, `created_at`, `updated_at`); no camelCase, no translation layer. [project-context.md#Data, format & error contract]
- **One git path / derive-on-read** are not directly exercised by 1.6 (no git), but `list_tasks` must NOT shell out to git and must NOT persist or read derived state — it reads the source-of-record `task`/`task_worktree` rows.

### Critical gotchas (carry into implementation)
- **`done` is terminal — reject ALL `done → *`**, including `done → done`. The legal rule is `src != 'done'`. A task in `done` is re-activated only by a **new `create_task`**, never `update_task` (epics AC2).
- **AC4 needs no special code** — it falls out of `status='done'` + the existing `status != 'done'` active-predicate. The MUST-PASS regression: `update_task(done)` then `create_task(same_slug)` **succeeds**; `update_task(review)` then `create_task(same_slug)` **rejects** `ActiveTaskConflict` (review is active). This is the same seam Story 1.3 flagged highest-risk (epics.md:226) — keep it green.
- **Preserve `created_at`.** The `UPDATE` sets only the provided field(s) + `updated_at`. A regression where `update_task` overwrites `created_at` is a silent data bug — assert `created_at` unchanged in the test.
- **Remove `ping` AND its test.** Forgetting either fails the gate: leaving `ping` → AC6's exactly-5 test fails; removing `ping` but leaving `test_handshake_and_ping_roundtrip` → that test fails on a missing tool. Also drop the now-unused `now_iso` import in `server_factory.py` or ruff `F401` fails. [server_factory.py:43,60,69-72; test_server_factory.py:50-70]
- **FastMCP plain-dict serialization:** a tool returning a `dict` arrives as JSON **text** (`content[0].text`), `structuredContent=None` — parse the text in round-trip tests. In-process base URL must be `http://127.0.0.1:<port>`; wrap test bodies in `async with app.router.lifespan_context(app)`. [project-context.md:72-76]
- **Empty-string filters mean "no filter".** `list_tasks(repo="")`/`list_tasks(status="")` must behave as unfiltered, not match-nothing/match-cwd — replicate the 1.5 fix (worktrees.py:61). [deferred-work.md#story-1-5]
- **`run_git` / asyncio:** not used by 1.6 core, but tests that call `create_task` (AC4 regressions, `list_tasks` fixtures) build `GitRunner()`/`RepoLockRegistry()`/`Store` **inside** the `asyncio.run()` body (asyncio objects bind to the running loop) — mirror 1.3-1.5 test scaffolding. No `pytest-asyncio`.

### 🛑 Git safety in tests — HARD RULE, gate-enforced (read before writing any test)
Most 1.6 tests are pure-Store (no git → no safety surface). But the **AC4 slug-reuse regressions** and the **`list_tasks` multi-repo fixtures** call `create_task`, which spawns **real** git (`worktree add`, `branch`). A test pointing git at a path resolving to THIS repo mutates this working tree — the incident that once destroyed `master`. Enforced by two guards (landed `8178e7a`):
- `tests/conftest.py` autouse `_guard_project_repo_untouched` — snapshots refs/HEAD, asserts byte-identical after every test.
- `tests/test_git_safety.py` — AST-scans the test tree; every git subprocess MUST pass `-C <repo>` AND `env=` (GIT_*-stripped); `os.system`/`os.popen` to git forbidden.
Therefore: any test that creates a task uses the **`tmp_git_repo`** fixture (real repo, one commit on `main`, GIT_*-stripped env, `-C` targeted — `tests/conftest.py`). For pure `update_task`/`list_tasks` status logic, you can **seed `task`/`task_worktree` rows directly via the Store** (no git at all) — prefer that for the transition-matrix table test to keep it fast and git-free. [Source: project-context.md#Git safety in tests; tests/conftest.py; tests/test_git_safety.py]

### Previous-story (1.5) intelligence that applies directly
- **Test style proven 1.2–1.5:** unit-test core directly with injected `GitRunner()`/`RepoLockRegistry()`/tmp-file `Store` (+ `tmp_git_repo` only where git is needed); drive with `asyncio.run()`; assert outcomes on the DB (`get_task`, `count_worktrees`) and, where git is involved, the filesystem. No `pytest-asyncio`, no new dep, no `slow` test. [Source: 1-5 story §Testing standards]
- **Empty-`repo`-filter fix** (1.5 review patch, worktrees.py:61): replicate in `list_tasks`. [Source: 1-5 story Review Findings / deferred-work.md#story-1-5]
- **Handler/closure/round-trip patterns** were extended verbatim for `list_worktrees`/`remove_worktree` in 1.5 (test_handlers.py, test_server_factory.py) — mirror those additions for `update_task`/`list_tasks`. [Source: 1-5 story File List]
- **Locale-fragile stderr classification** (deferred 1.4) does NOT affect 1.6 (no git stderr parsing in update/list). Noted so you don't chase it.

### Git / recent-work intelligence
- **Baseline commit `3c41a67`** ("1-5"). Stories 1.1-1.5 are implemented + reviewed-`done`. Tool surface at baseline: `ping`, `create_task`, `list_worktrees`, `remove_worktree` (4). 1.6 removes `ping`, adds `update_task`/`list_tasks` → final **5**.
- **Src tree present:** `core/{tasks,worktrees,mutator,slug}.py`, `git/{runner,repo_lock,porcelain}.py`, `store.py`, `tools/{handlers,models}.py`, `server_factory.py`, `config.py`, `util.py`, `errors.py`. **No new module needed** — `update_task`/`list_tasks` extend `core/tasks.py` + `store.py`. [Source: find src]
- Recent commit cadence (1-3 → 1-4 → 1-5) shows one commit per story after green gate + review; 1.6 follows the same.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`, `src/` layout; type hints on every public signature (`str | None`, `tuple[str, ...]`). [project-context.md#Technology Stack]
- **`mcp` 1.28.x FastMCP** — plain-`dict` tool returns serialize as JSON **text** content; `structuredContent` stays `None`. In-process ASGI base URL `http://127.0.0.1:<port>`; `ASGITransport` does NOT auto-run the lifespan. [project-context.md#Testing rules]
- **`aiosqlite` 0.22.x** — dynamic `SET` via parameterized placeholders; `cursor.rowcount` after `UPDATE` gives the matched-row count; `commit()` per mutation (existing Store pattern). No new SQL features needed.
- **No new dependency.** Pure stdlib + existing deps.

### Project Structure Notes
- **NEW:** none (no new module).
- **UPDATE:** `config.py` (+`TASK_STATUSES`/`ACTIVE_STATUSES`/`TERMINAL_STATUS` + the transition predicate, or predicate in `core/tasks.py`); `store.py` (+`update_task`, +`list_tasks`; reuse `get_task`/`list_worktree_links`/`count_worktrees`); `core/tasks.py` (+`update_task`, +`list_tasks`); `tools/models.py` (+`UpdateTaskIn`, +`ListTasksIn`); `tools/handlers.py` (+`update_task`, +`list_tasks` adapters); `server_factory.py` (−`ping`, +2 `@mcp.tool()` closures, docstring, drop dead `now_iso` import); **conditionally** `errors.py`/`architecture.md`/`project-context.md`/`test_errors.py` (only if Decision A adds `InvalidStatus`).
- **UNCHANGED (do not edit):** `core/{worktrees,mutator,slug}.py`, `git/*`, `middleware.py`, `cli.py`, `server.py`. **DB schema unchanged — no migration.**
- **DEFERRED, do NOT create:** `cache.py`, `projection.py`, `/state`, dashboard, `add_worktree`, any reconciliation sweep, live-git fan-out in `list_tasks`. [Source: architecture.md#Directory Structure; project-context.md; deferred-work.md]
- Runtime DB at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db` — tests use tmp-file/`:memory:` Stores; never under `src/` or the project repo.

### Testing standards
- `tests/` mirrors `src/`; `test_<module>.py`; async via `asyncio.run()` (no `pytest-asyncio`); reuse `tmp_git_repo` ONLY where a test creates a task (AC4 regressions, `list_tasks` multi-repo fixtures). Pure status-lifecycle tests seed rows via the Store (git-free).
- **Transition matrix:** a single parametrized table test over all 16 `(src,dst)` pairs (AC2 mandate) — 12 pass / 4 (`done→*`) reject.
- **AC6 surface:** enumerate via the in-process SDK handshake; assert the name set is exactly the 5 (ping absent) + schemas present.
- **Assert update outcomes on the DB:** after `update_task`, `get_task` shows the new status/description, advanced `updated_at`, unchanged `created_at`; on a rejected update, nothing changed.
- Everything green under the enforced gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dep, no `slow` test, no schema migration. [Source: project-context.md#Testing rules, #Code-quality gate; 1-3/1-4/1-5 stories]

### References
- [Source: epics.md#Story 1.6 (lines 294-325)] — user story + all 6 ACs verbatim; `update_task(task_id, status?, description?)`, four-state guard, transition matrix + `done`-terminal, `TaskNotFound`, slug-release-on-`done`, `list_tasks(status?, repo?)`, exactly-5 surface.
- [Source: epics.md:36-42] — FR-5 (update status/description, `updated_at` bump, not-found), FR-6 (four-state set, meanings, `done` terminal/folded), FR-7 (list_tasks filters + model fields + links), FR-11 (5-tool surface).
- [Source: epics.md:119] — Story 1.6 owns the 4-status set + the `done`-terminal transition matrix; active=non-done pinned in 1.3.
- [Source: epics.md:226] — the `status != 'done'` active predicate (review conflicts, done releases) — the AC4 regression seam.
- [Source: architecture.md:319-338] — `task`/`task_worktree` schema, status `CHECK`, `created_at` preserved / `updated_at` advanced on update.
- [Source: architecture.md:44-47, 109-110] — four-state meanings (`blocked`=awaiting input, `review`=awaiting operator review/non-merge/active, `done`=terminal).
- [Source: architecture.md:421-426, 612-626] — 5-tool surface, `{ok,data,error}` envelope, typed-error-as-data, `Internal` for unexpected.
- [Source: architecture.md:466-473] — error taxonomy (no `InvalidStatus` — see Decision A); `TaskNotFound`, `InvalidTaskName` present.
- [Source: architecture.md:728, 714, 606-608] — `core/tasks.py` is the update/list home; `*In` models in `tools/models.py`; core takes plain args.
- [Source: project-context.md] — SDK seam, snake_case/error contract, `now_iso()` discipline, "this file wins over architecture pseudo-code" (cache/projection/list-view = Epic 2), git-safety-in-tests, tool surface (5, final; `ping` is the 1.1 throwaway).
- [Source: deferred-work.md#story-1-5] — empty-`repo`-filter fix to replicate; nothing else from prior deferrals blocks 1.6.
- [Source: src/dev_helper_mcp/{store.py, core/tasks.py, core/worktrees.py, tools/handlers.py, tools/models.py, server_factory.py, errors.py, config.py, util.py}] — exact substrate APIs + the patterns to mirror.
- [Source: 1-5-list-and-remove-worktrees.md] — test style, handler/closure extension precedent, the two-coexisting-"closed"-semantics (1.5 deletes on last-worktree-remove; 1.6 sets `done` and keeps).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Claude Opus 4.8, 1M context)

### Debug Log References

- Full gate green: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` → **168 passed, 1 deselected** (the `slow` uvicorn smoke test). Baseline before the story was 133 passed; +35 tests (incl. the 16-case parametrized transition matrix).
- No new runtime dependency, no new `slow`-marked test, no schema migration (the `task` table already carried `created_at`/`updated_at` + the status `CHECK`).

### Completion Notes List

- **Decision A — RESOLVED (operator confirmed: add `InvalidStatus`).** New `DevHelperError` subclass `code="InvalidStatus"` covers both AC1 (out-of-set status) and AC2 (illegal transition) with `details.reason ∈ {"not_in_set", "illegal_transition"}` so the agent can distinguish. Propagated to `errors.py`, `tests/test_errors.py` (taxonomy table), `architecture.md` (Step-4 taxonomy + Invariant-2 code list), and `project-context.md` (error-code list) — pattern change made in architecture.md, then propagated.
- **Tool surface is now exactly 5** (`create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks`). The Story 1.1 throwaway `ping` tool was removed along with its now-dead `now_iso` import in `server_factory.py`; the `ping` round-trip test was repurposed into a `list_tasks` handshake round-trip, and a new `test_exactly_five_tools_advertised` asserts the exact name set + per-tool input schemas over the in-process SDK handshake (AC6).
- **`update_task` is UPDATE-only, no mutex, no git** (Invariant 12/AR-14). Order: resolve→validate status→validate transition→apply. `legal_transition(src,dst) := dst in TASK_STATUSES and src != TERMINAL_STATUS` (12 legal / 4 illegal — `done` terminal, incl. `done→done`). `created_at` preserved, `updated_at = now_iso()`.
- **AC4 falls out of `status='done'` + the existing `status != 'done'` active-predicate** — no special code. Regression proven: `create → update_task(review) → create same slug` rejects `ActiveTaskConflict`; `create → update_task(done) → create same slug` succeeds. `done` keeps the row + links (NOT deleted — that is `remove_worktree`'s distinct last-worktree path).
- **`list_tasks` is a Store read** (not Epic 2's live-git/cache view): `SELECT` from `task` + nested `task_worktree` links (reusing the `list_worktree_links` JOIN shape), sorted by `task_id`/`repo_path`. Empty-string `status`/`repo` filters mean "no filter" (replicates the 1.5 `worktrees.py:61` fix); a non-empty `repo` is abspath'd. A `repo` filter returns only tasks touching that repo, links limited to it.
- **`UpdateTaskIn.status` kept as `str | None` (not `Literal`)** so an out-of-set value is rejected by core as typed-error-as-data inside the handler try/except, not a raw Pydantic `ValidationError` escaping the envelope.
- Adapter seam preserved — `core/tasks.py` imports no `mcp`/`starlette` (`tests/test_adapter_seam.py` green). All git in tests targets `tmp_git_repo`/`tmp_path` only; pure status-lifecycle + transition-matrix tests seed rows directly via the Store (git-free).

### File List

- `src/dev_helper_mcp/config.py` — added `TASK_STATUSES`/`ACTIVE_STATUSES`/`TERMINAL_STATUS` + `legal_transition()` predicate.
- `src/dev_helper_mcp/errors.py` — added `InvalidStatus(DevHelperError)` (Decision A).
- `src/dev_helper_mcp/store.py` — added `update_task` (dynamic-SET UPDATE, preserve `created_at`, return matched-bool) + `list_tasks` (task SELECT + nested links, filters).
- `src/dev_helper_mcp/core/tasks.py` — added `update_task` (four-state guard + transition matrix + `TaskNotFound`) + `list_tasks` (Store read + empty-filter normalization); updated imports.
- `src/dev_helper_mcp/tools/models.py` — added `UpdateTaskIn` + `ListTasksIn`.
- `src/dev_helper_mcp/tools/handlers.py` — added `update_task` + `list_tasks` adapters; updated imports.
- `src/dev_helper_mcp/server_factory.py` — removed `ping` tool + dead `now_iso` import; added `update_task` + `list_tasks` `@mcp.tool()` closures; updated `build_mcp` docstring + imports.
- `tests/test_store.py` — added store `update_task` (3) + `list_tasks` (3) tests.
- `tests/test_tasks.py` — added core `update_task`/`list_tasks` tests + the parametrized 4×4 transition-matrix table test (16 cases) + AC4 slug-reuse regressions; updated imports.
- `tests/test_handlers.py` — added `update_task` (success/not-found/invalid-status) + `list_tasks` envelope tests; updated imports.
- `tests/test_server_factory.py` — repurposed the `ping` round-trip into a `list_tasks` handshake; added the AC6 exactly-5-tools enumeration test + an `update_task`/`list_tasks` round-trip.
- `tests/test_errors.py` — added `InvalidStatus` to the taxonomy table.
- `_bmad-output/planning-artifacts/architecture.md` — added `InvalidStatus` to the Step-4 taxonomy + Invariant-2 code list (Decision A propagation).
- `_bmad-output/project-context.md` — added `InvalidStatus` to the stable error-code list (Decision A propagation).

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-24 | Story 1.6 drafted (ready-for-dev): `update_task` + `list_tasks` + remove `ping` to lock the exactly-5 tool surface. Open decision A (error code for invalid status / illegal transition — recommend new `InvalidStatus`). |
| 2026-06-24 | Implemented Story 1.6 (status → review). Decision A resolved (operator confirmed `InvalidStatus`). Added `update_task` (four-state guard + `done`-terminal 4×4 transition matrix + `TaskNotFound`) and `list_tasks` (filterable Store read with nested per-repo links) across config/store/core/models/handlers/server_factory; removed the `ping` seed to pin the exactly-5 tool surface (AC6). Propagated `InvalidStatus` to errors/architecture/project-context/test_errors. Gate green: 168 passed, 1 deselected. |
| 2026-06-24 | Code review (adversarial, 3 layers, Opus 4.8): 0 AC violations. 2 decision-needed resolved by operator (no-op update → guard/return-unchanged; `list_tasks` filter → left permissive). 2 patches applied to `core/tasks.py` `update_task`: (1) no-op guard (no DB write / no `updated_at` bump when both fields `None`); (2) consume `store.update_task`'s matched-bool → raise `TaskNotFound` on no-match (close TOCTOU phantom-success). +2 regression tests. Gate green: 170 passed, 1 deselected. Status → done. |
