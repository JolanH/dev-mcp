---
baseline_commit: 8178e7a
---

# Story 1.5: List and remove worktrees

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent or the developer,
I want to list worktrees across all tracked repos and remove one when its work is done,
so that I can see every isolated checkout and clean it up safely without touching the task's other repos.

## Acceptance Criteria

_Verbatim from epics.md#Story 1.5 (lines 271–292). BDD Given/When/Then._

1. **`list_worktrees` — live, filterable, joined.**
   **Given** tasks with worktrees across several repos,
   **When** I call `list_worktrees(repo?, task_id?)`,
   **Then** it returns each worktree's `repo_path`, `worktree_path`, `branch`, and linked `task_id`/`status`, filtered as requested, **derived from per-repo `git worktree list --porcelain -z` (not a stale cache)**.

2. **Remove one worktree — others unaffected.**
   **Given** a worktree identified by `task_id`+`repo` (or by path),
   **When** I call `remove_worktree`,
   **Then** that repo's working tree is removed and de-tracked **and** its `task_worktree` row dropped;
   **And** the task's worktrees in other repos are unaffected.

3. **`DirtyWorktree` guard / `force`.**
   **Given** a worktree with uncommitted changes,
   **When** `remove_worktree` is called without `force`,
   **Then** `DirtyWorktree` is returned and **nothing changes**;
   **And** with `force=true` it is removed.

4. **`UnmergedBranch` guard / `force_unmerged_branch`.**
   **Given** `delete_branch=true` for a branch with unmerged commits,
   **When** `remove_worktree` is called without `force_unmerged_branch`,
   **Then** `UnmergedBranch` is returned, **surfacing the unmerged-commit count first**;
   **And** with `force_unmerged_branch=true` the branch is deleted.

5. **Last worktree removed → task closed/detached.**
   **Given** a task whose last remaining worktree is removed,
   **When** the removal completes,
   **Then** the task record is marked closed/detached.

## Tasks / Subtasks

> **Build order** (each step independently testable): `git/porcelain.py` pure parser (+ fixture corpus) → `core/worktrees.py` list logic (live git × store join) → `list_worktrees` store query + handler + tool registration → `core/worktrees.py` guarded remove (two guards, two flags) → `remove_worktree` store deletes + AC5 task-close + handler + tool → concurrency test (per-repo mutex) → gate green.

> **Scope fence (read first — anti-scope-creep):** this story adds the read tool `list_worktrees`, the guarded `remove_worktree`, and the porcelain parser ONLY. **NO** `update_task`/`list_tasks` (Story 1.6), **NO** `cache.py`/`projection.py`/`/state`/dashboard (Epic 2 — and AC1 explicitly forbids reading a cache), **NO** status-transition matrix (Story 1.6 owns the four-state lifecycle; 1.5 touches status only as required by AC5), **NO** `add_worktree`/incremental repo attach (deferred project-wide, AR-13), **NO** startup reconciliation / crash-safety (v1 non-goal). Do not modify `create_task`'s rollback path or `GitRepoMutator` (see Dev Notes — the user-facing remove path is a *separate* code path from create's force-teardown compensation).

- [x] **Task 1 — `git/porcelain.py`: pure parser of `git worktree list --porcelain` (AC: 1)**
  - [x] Create `src/dev_helper_mcp/git/porcelain.py` — a **pure, SDK-free, I/O-free** parser. It takes the raw `bytes` stdout of `git worktree list --porcelain` and returns a list of parsed records. It does **NOT** spawn git (that is `git/runner.py`'s sole job — Invariant 1 / data boundary) and imports no `mcp`/`starlette` (core-adjacent; seam-scanned).
  - [x] Parse the porcelain format, **delimiter-agnostic** (auto-detects the NUL `-z` form vs. the default newline form — see Completion Notes re: git 2.34 lacking `worktree list -z`). Records are separated by a blank line; within a record each attribute line is terminated by the line separator. Extract per worktree: `worktree` (absolute path), `branch` (e.g. `refs/heads/agent/<slug>` → normalize to `agent/<slug>`), `HEAD` (sha), and the boolean flags `detached`, `bare`, `locked`, `prunable` (presence of the keyword line ⇒ true). `.decode(errors="replace")` — porcelain bytes may carry unicode paths.
  - [x] Returns a frozen dataclass `WorktreeEntry(path, branch, head, detached, locked, prunable, bare)`. `branch` is `None` for a detached HEAD. The parser is faithful (parses ALL records incl. main/bare); filtering to the agent view is the join's job (Task 2).
  - [x] **Tests** (`tests/test_porcelain.py`): a **static fixture corpus** of sample `--porcelain` byte blobs (both newline and NUL forms) — covers detached HEAD, locked, prunable, bare, unicode path, path-with-space, empty input, and a missing trailing blank line. Pure-parser unit tests (no git spawn, no tmp repo → no git-safety surface). 9 tests green.

- [x] **Task 2 — `core/worktrees.py` list logic: live git × store join (AC: 1)**
  - [x] Create `src/dev_helper_mcp/core/worktrees.py` (SDK-free core; plain args; raises `DevHelperError`). This is the architecture's named home for per-repo list/remove logic (architecture.md#Complete Project Directory Structure line 727).
  - [x] `async def list_worktrees(*, repo: str | None, task_id: str | None, runner, store) -> list[dict]`:
    1. Read the **stored links** from the Store (new method, Task 3): `(task_id, repo_path, branch, worktree_path, status)` rows, filtered by `repo` / `task_id` when provided.
    2. Determine the **set of tracked repos** to scan = distinct `repo_path` from those links (filtered). For each repo, fan out `runner.run_git(repo, ["worktree", "list", "--porcelain", "-z"], pool=Pool.READ)` — **READ pool** (3s, sem=2), parse via `porcelain.py`. This is the live derive (AC1: not a cache).
    3. **LEFT-JOIN** stored links onto live porcelain on `(repo_path, branch)`. Emit one entry per link: `{repo_path, worktree_path, branch, task_id, status}`. A link whose branch is **absent** from its repo's live porcelain is still returned but flagged orphaned (e.g. `orphaned: true`) — **never auto-deleted, never auto-`done`** (derive-on-read rule, architecture.md#Data Architecture lines 344-348). Do NOT delete rows on the read path.
    4. Apply the `repo` / `task_id` filters to the result.
  - [x] No per-repo mutex on this read path (Invariant 12 / AR-14: read/refresh ops do not take the mutation mutex). No destructive git (Invariant 10).
  - [x] **Tests** (`tests/test_worktrees.py`): create 2 `tmp_git_repo`s, `create_task` across both, then `list_worktrees()` → asserts both entries with correct `repo_path`/`worktree_path`/`branch`/`task_id`/`status`; assert `repo=` and `task_id=` filters narrow correctly; assert an orphaned link (branch deleted out-of-band) is returned flagged, not dropped.

- [x] **Task 3 — `list_worktrees` Store query + adapter + tool registration (AC: 1)**
  - [x] **Store** (`store.py`): add a read method, e.g. `async def list_worktree_links(self, *, repo: str | None = None, task_id: str | None = None) -> list[dict]` — a parameterized SELECT joining `task_worktree` ⋈ `task` on `task_id`, returning `{task_id, repo_path, branch, worktree_path, status}`. Parameterized queries only (no string interpolation). Read-only; no schema change.
  - [x] **Model** (`tools/models.py`): `class ListWorktreesIn(BaseModel)` with `repo: str | None = None`, `task_id: str | None = None`.
  - [x] **Handler** (`tools/handlers.py`): `async def list_worktrees(inp: ListWorktreesIn, *, deps: ToolDeps) -> dict` — unpack to core args, wrap result in `{ok, data, error}`; typed `DevHelperError` → `{ok:false, error:…}`; unexpected → `Internal` (mirror the `create_task` handler exactly, lines 38-55).
  - [x] **Register** (`server_factory.py`): add an `@mcp.tool() async def list_worktrees(repo: str | None = None, task_id: str | None = None) -> dict` closure mirroring the `create_task` closure (lines 72-94) — read `holder.deps`, guard the not-ready window (`server not ready` → `Internal`), build `ListWorktreesIn`, delegate to `handlers.list_worktrees`.
  - [x] **Tests** (`tests/test_tools.py` or extend): in-process `httpx.ASGITransport` round-trip (or direct handler test) asserting the envelope shape, **snake_case** keys, and error-as-data.

- [x] **Task 4 — `core/worktrees.py` guarded remove: two guards, two flags (AC: 2, 3, 4)**
  - [x] `async def remove_worktree(task_id, repo, *, delete_branch=False, force=False, force_unmerged_branch=False, runner, locks, store) -> dict`. **Acquire the per-repo mutex** for `repo` (Invariant 12 / AR-14 — `remove_worktree` is a mutation; `RepoLockRegistry.lock_for(abspath)`), hold it across the whole critical section, release in `finally`. All destructive git on **`Pool.MUTATION`** (~120s, sem=4).
  - [x] **Resolve the target:** look up the `task_worktree` link by `(task_id, repo)` → `worktree_path` + `branch`. If no such link (or unknown slug) → raise `TaskNotFound`. (Accept an explicit path as an alternative identifier per AC2 "or by path" — secondary; primary is `task_id`+`repo`.)
  - [x] **Worktree removal + `DirtyWorktree`/`LockedWorktree` guard (AC3):**
    - When `force=false`: run `git -C <repo> worktree remove <worktree_path>` (the **safe** variant, no `--force`). If git refuses, classify stderr: uncommitted/modified/untracked → **`DirtyWorktree`**; `is locked` → **`LockedWorktree`**. On the guard, **nothing changes** — do not delete rows, do not delete the branch, re-raise the typed error.
    - When `force=true`: run `git worktree remove --force <worktree_path>` (bypasses dirty/locked). Note `--force` covers BOTH dirty and locked blast radii (architecture.md lines 474-477).
  - [x] **Branch deletion + `UnmergedBranch` guard (AC4)** — only when `delete_branch=true`:
    - Worktree must be removed FIRST (git refuses to delete a branch checked out in a worktree) — same ordering as the create-rollback teardown.
    - When `force_unmerged_branch=false`: `git -C <repo> branch -d <branch>` (**safe** delete). If git refuses ("not fully merged"), compute the **unmerged-commit count first** via a **READ-pool** op — `git -C <repo> rev-list --count <branch> --not --all` (commits on the branch reachable from nothing else) — and raise **`UnmergedBranch`** with the count in `details` (e.g. `details={"branch":…, "unmerged_commits": N}`). The worktree was already removed at this point; document that the worktree removal is NOT rolled back (the branch still exists for re-deletion) — surface clearly. _(See Dev Notes "ordering & partial-failure" — flag for reviewer.)_
    - When `force_unmerged_branch=true`: `git branch -D <branch>` (force).
  - [x] **`force_unmerged_branch` is distinct from `force`** — forcing a dirty-worktree removal must NOT silently authorize dropping unmerged branch commits, and vice versa. Two flags, two blast radii (architecture.md#Invariant 10).
  - [x] Reuse the project's git path through `runner.run_git` ONLY (Invariant 1) — never `subprocess`. Decode `GitResult.stderr` with `errors="replace"`. **Do NOT** route this through `GitRepoMutator.remove` — that primitive is the unconditional `--force`/`-D` teardown for `create_task` *rollback of a just-created clean worktree*; the user-facing remove needs the safe variants + guards. (See Dev Notes — this resolves the deferred 1.4 review finding.)

- [x] **Task 5 — Store deletes + AC5 task close + adapter + tool (AC: 2, 5)**
  - [x] **Store** (`store.py`): add `async def delete_worktree(self, task_id: str, repo_path: str) -> None` (DELETE one `task_worktree` row by PK `(task_id, repo_path)`, commit). `count_worktrees(task_id)` and `delete_task(task_id)` already exist — reuse both. **No status setter** — AC5 deletes the task row (decision settled, Dev Notes).
  - [x] **Removal persistence ordering** (inside the mutex critical section, after the destructive git succeeds): drop the `task_worktree` row for `(task_id, repo)`. Then **AC5**: `if count_worktrees(task_id) == 0: await store.delete_task(task_id)` (cascade clears the last link). Sibling rows for the same `task_id` in OTHER repos are untouched (AC2). Persist AFTER git success (mirror create's rows-last discipline).
  - [x] **Model** (`tools/models.py`): `class RemoveWorktreeIn(BaseModel)` — `task_id: str`, `repo: str`, `delete_branch: bool = False`, `force: bool = False`, `force_unmerged_branch: bool = False`.
  - [x] **Handler + register**: `handlers.remove_worktree` (envelope) + a `server_factory.py` `@mcp.tool()` closure mirroring `create_task` (deps guard, build `RemoveWorktreeIn`, delegate).
  - [x] **Tests** (`tests/test_worktrees.py`): real `tmp_git_repo`s. (a) AC2: 2-repo task, remove one → that worktree gone + de-tracked (`git worktree list` no longer shows it) + its row dropped; the OTHER repo's worktree + row intact. (b) AC3: dirty the worktree (write an uncommitted file) → remove without `force` raises `DirtyWorktree`, asserts worktree + row still present; with `force=true` → removed. (c) AC4: `delete_branch=true` on a branch with an unmerged commit → `UnmergedBranch` with `details["unmerged_commits"]` count, branch still present; `force_unmerged_branch=true` → branch gone. (d) AC5: single-repo task, remove its only worktree → task record closed/detached (assert per chosen Decision). Use the `tmp_git_repo` `-C`+`env`-stripped helpers ONLY (git-safety).

- [x] **Task 6 — Concurrency: per-repo mutex serializes same-repo removal (AC: 2; Epic 1 Risk AC)**
  - [x] **Test** (`tests/test_concurrency.py`, new or extend): assert the per-repo async mutex serializes a `remove_worktree` against a concurrent same-repo mutation (e.g. two coroutines via `asyncio.gather` on the same repo) — interleaving is serialized, no torn state. `list_worktrees` (read) does NOT take the mutex. Drive with `asyncio.run`; no `pytest-asyncio`. (Epic 1 Risk AC: "AR-14 per-repo mutex serializes same-repo mutations".)

- [x] **Task 7 — Gate green + git-safety + seam compliance (AC: all)**
  - [x] `git/porcelain.py` and `core/worktrees.py` import no `mcp`/`starlette` (verify `tests/test_adapter_seam.py` stays green — it scans `core/`+`git/`).
  - [x] **Every** git op in tests targets `tmp_git_repo`/`tmp_path` repos only (HARD git-safety rule — `tests/test_git_safety.py` static scan + autouse `_guard_project_repo_untouched` both enforce it). Real `worktree remove`/`branch -d/-D` run against tmp worktrees only.
  - [x] Full gate green: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. **No** new dependency, **no** `slow`-marked test.

### Review Findings

_Adversarial code review (2026-06-23): Blind Hunter + Edge Case Hunter + Acceptance Auditor. All 5 ACs verified SATISFIED by the Auditor; invariants (SDK seam, one-git-path, per-repo mutex on remove/not list, persist-after-git, derive-on-read, snake_case, GitRepoMutator unchanged) all hold. Findings below._

**Decision-needed:** _(resolved 2026-06-23 → moved to Patch)_

- Resolved (option 1): surface a `worktree_already_gone` flag rather than silently conflating live-removal with orphan cleanup. See the corresponding Patch item.

**Patch:**

- [x] [Review][Patch] `remove_worktree` already-gone case surfaces a flag: on the `_is_not_a_worktree` idempotent fall-through, add `worktree_already_gone: true` to the result `data` so callers distinguish a live worktree removal from an orphan-link cleanup (resolved decision, option 1) [src/dev_helper_mcp/core/worktrees.py:177]
- [x] [Review][Patch] Branch-already-gone wedges the link: `git branch -d/-D` returning "not found" is not classified, raises `Internal`, and `delete_worktree` never runs → the link is permanently un-removable (every retry re-raises `Internal`). Treat branch-not-found as idempotent success and fall through to persistence (symmetric to the `_is_not_a_worktree` handling) [src/dev_helper_mcp/core/worktrees.py:237-249] (blind+edge)
- [x] [Review][Patch] `list_worktrees` lets `run_git` raise abort the whole call: only `returncode != 0` is degraded, but `run_git` *raises* `GitTimeout`/`Internal` (hung/contended repo, missing git) — one bad repo poisons every other repo's links, contradicting the function's own "never raise, never delete" comment. Catch `DevHelperError` per-repo in the loop and degrade like `rc != 0` [src/dev_helper_mcp/core/worktrees.py:69-84] (edge)
- [x] [Review][Patch] `_unmerged_commit_count` rev-list lacks a `--` end-of-options sentinel before the branch/ref args; add `--` for defense against option-like ref names. (The degraded `0`-on-error is a best-effort *informational* preview — the `UnmergedBranch` guard still fires regardless — so document it rather than treat 0 as authoritative) [src/dev_helper_mcp/core/worktrees.py:266-276] (blind+edge+auditor)
- [x] [Review][Patch] Empty-string `repo` filter silently becomes a CWD filter: `repo is not None` is true for `""`, so `os.path.abspath("")` resolves to the cwd instead of "no filter". Treat a falsy `repo` as no-filter (or reject empty in `ListWorktreesIn`) [src/dev_helper_mcp/core/worktrees.py:61] (edge)

**Deferred** (logged to deferred-work.md):

- [x] [Review][Defer] Vanished/renamed repo: an orphaned link whose repo dir is gone is not cleanable (`git worktree remove` → unclassified non-zero → `Internal`) — reconciliation is an explicit v1 non-goal / out of 1.5 scope [src/dev_helper_mcp/core/worktrees.py]
- [x] [Review][Defer] Moved worktree (`git worktree move`): the live path isn't reconciled — `orphaned` joins on `(repo_path, branch)` per spec, so a branch present at a different path reads `orphaned:false` with a stale `worktree_path`. Out of scope (move/add deferred project-wide) [src/dev_helper_mcp/core/worktrees.py]
- [x] [Review][Defer] Detached-HEAD agent worktree reads `orphaned:true` (parser yields `branch=None`, filtered from the live set) — heuristic limitation of the branch-based join; agents don't normally detach [src/dev_helper_mcp/git/porcelain.py]
- [x] [Review][Defer] Cross-repo same-task concurrent removal isn't serialized for the shared `task` row (the mutex is per-`repo`); `task_closed` may be reported by both and `count`/`delete` ordering is unserialized — cosmetic, `delete_task` is idempotent, no corruption [src/dev_helper_mcp/core/worktrees.py]
- [x] [Review][Defer] AC2 "or by path" alternate identifier not implemented though the subtask is checked — the story marks it secondary/optional; the binding ACs use `task_id`+`repo` [src/dev_helper_mcp/core/worktrees.py]
- [x] [Review][Defer] `LockedWorktree` classifier has no test coverage (no `git worktree lock` test) — low risk, additive behavior; add coverage later [tests/test_worktrees.py]
- [x] [Review][Defer] Concurrency serialization test uses a fixed `asyncio.sleep(0.05)` and only proves blocking at `acquire()`, not that the lock is held across the whole git+persist critical section — test hardening; matches the existing suite's timing pattern [tests/test_concurrency.py]
- [x] [Review][Defer] AC1 `-z` literal deviation (git 2.34 lacks `worktree list -z`) — reconcile the epics AC text or pin git ≥ 2.36; operator-approved, parser already `-z`-ready (doc follow-up) [src/dev_helper_mcp/git/porcelain.py]

_Dismissed as noise (3): "cascade clears the final link" comment nuance (code is correct — `delete_worktree` then count→`delete_task`); transient half-removed view on concurrent list (by-design, read takes no mutex); porcelain flag value stored as string-vs-`True` when a reason is present (works correctly via key-presence check)._

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
1.5 adds two MCP tools + one parser. The single biggest trap is **pulling Epic 2 forward**:
- **AC1 reads LIVE git, NOT a cache.** The architecture doc describes `list_worktrees` returning a `CacheSnapshot` from an in-memory cache via `projection.py`/`cache.py`. **That is Epic 2 and those modules DO NOT EXIST.** epics AC1 says explicitly "*not a stale cache*", and project-context.md + the Story 1.4 scope fence place `cache.py`/`projection.py`/`/state` in Epic 2. **Where the architecture's pseudo-code (CacheSnapshot, "rebuild snapshot + swap cache ref") contradicts this, project-context wins** (project-context.md#Usage Guidelines: "this file wins — it reflects working code"). So: `list_worktrees` fans out `git worktree list --porcelain -z` per repo on the READ pool and joins the store — no cache, no `projection.py`, no `cache.refresh()`.
- **`update_task`/`list_tasks`, the four-state transition matrix, and `TaskNotFound`-as-an-AC are Story 1.6.** 1.5 raises `TaskNotFound` only as the natural "unknown slug" error for `remove_worktree`, and touches status only for AC5.
- **`add_worktree` / incremental repo attach is deferred project-wide** (AR-13). The repo set is fixed at create; `remove_worktree` is the only post-create worktree mutation.

### Granularity decision (settled from the ACs)
`remove_worktree` removes **ONE** worktree (one `(task_id, repo)`), not the whole task. epics AC2 is explicit: "that repo's working tree is removed … the task's worktrees in **other repos are unaffected**." (The architecture agent speculated task-level removal; the epics AC is binding and overrides.) Removing the *last* worktree triggers AC5's task-close.

### ✅ AC5 "marked closed/detached" — DECISION SETTLED (2026-06-23): DELETE the task row
On removing a task's **last** worktree, **DELETE the `task` row** (`store.delete_task(task_id)` already exists; `ON DELETE CASCADE` + `foreign_keys=ON` clears the final `task_worktree` link). This is the confirmed semantics for "closed/detached": the task ceases to be tracked; a later same-slug create is a fresh insert. Rationale: cleanest scope boundary — 1.5 does removal only and **never mutates `status`** (the four-state lifecycle is Story 1.6's domain), and it sidesteps the derive-path "never auto-`done`" rule entirely.
- Do **NOT** add a status setter or set `status='done'` here — that was the rejected alternative; leave status mutation to Story 1.6.
- Concretely: after dropping the removed worktree's link, `if store.count_worktrees(task_id) == 0: await store.delete_task(task_id)` (or equivalently delete the `task` row and let the cascade clear the last link). When other repos' worktrees remain, only the single `task_worktree` row is dropped and the task row stays.

### 🟢 This story resolves the deferred 1.4 review finding — keep the two paths separate
Story 1.4's code review deferred: *"`remove`'s `branch -D` force-deletes unguarded at the reusable seam — guard before Story 1.5 reuses it for `remove_worktree`"* (deferred-work.md#story-1-4). **Resolution: do NOT reuse `GitRepoMutator.remove` for the user-facing path.** That primitive intentionally uses unconditional `git worktree remove --force` + `git branch -D` because it tears down a **just-created, clean, brand-new** worktree during `create_task` rollback — force is correct and safe there. The user-facing `remove_worktree` needs the **safe** variants (`worktree remove` / `branch -d`) plus the two guards, escalating to `--force`/`-D` only on the explicit flags. So `core/worktrees.py.remove_worktree` runs its own guarded git; `GitRepoMutator` is left unchanged (still correct for rollback). Two call sites, two blast radii — do not unify them.

### What the substrate already gives you (verified shipped 1.1–1.4 — reconcile against THIS, not the architecture pseudo-code)
- **`git/runner.py` → `GitRunner.run_git(repo, args, *, pool) -> GitResult`** — `Pool.READ` (3s, sem=2) for `worktree list` + the unmerged-count `rev-list`; `Pool.MUTATION` (~120s, sem=4) for `worktree remove`/`branch -d/-D`. **Non-zero exits are RETURNED, not raised** (`GitResult(returncode, stdout: bytes, stderr: bytes)`); only `GitTimeout`/`NotAGitRepo`/`Internal` are raised. Classify yourself from `returncode`/`stderr`. [Source: src/dev_helper_mcp/git/runner.py:84]
- **`git/repo_lock.py` → `RepoLockRegistry.lock_for(repo_path: str) -> asyncio.Lock`** — per-repo lock; lazily minted. `remove_worktree` acquires `lock_for(os.path.abspath(repo))`; `list_worktrees` does not. [Source: src/dev_helper_mcp/git/repo_lock.py:35]
- **`store.py`** — the ONLY DB opener (SDK-free core). Existing methods usable as-is: `get_task(task_id) -> dict|None`, `count_worktrees(task_id) -> int`, `delete_task(task_id)` (plain DELETE; cascade clears links). **You add**: `list_worktree_links(repo?, task_id?)` (SELECT join) + `delete_worktree(task_id, repo_path)` (DELETE one link). Schema is UNCHANGED — two tables, `(task_id, repo_path)` PK, `ON DELETE CASCADE`, `foreign_keys=ON`. Parameterized queries only. [Source: src/dev_helper_mcp/store.py]
- **`config.py`** — `worktree_path_for(repo: Path, slug) -> <repo>.worktrees/<slug>`, `branch_name_for(slug) -> agent/<slug>`. Pools/timeouts/`GIT_ENV`/`GIT_CONTEXT_VARS` already defined. **Append** any new tunable here (no magic numbers in modules) — 1.5 likely needs none. [Source: src/dev_helper_mcp/config.py]
- **`errors.py`** — `DirtyWorktree`, `UnmergedBranch`, `LockedWorktree`, `TaskNotFound`, `Internal` **already defined** as `DevHelperError` subclasses (`code` = the class name; `as_dict() -> {code, message, details}`). **Only raise them — add NO new error classes** (taxonomy is complete, stable contract). [Source: src/dev_helper_mcp/errors.py:49-67]
- **`tools/handlers.py`** — the envelope adapter pattern to mirror exactly: unpack `*In` → core args → `{ok, data, error}`; `except DevHelperError → exc.as_dict()`; `except Exception → Internal("unexpected error")` (no stack-trace leak). [Source: src/dev_helper_mcp/tools/handlers.py:38-55]
- **`tools/models.py`** — Pydantic `*In` models live ONLY here (one per tool). [Source: src/dev_helper_mcp/tools/models.py]
- **`server_factory.py`** — the `@mcp.tool()` closure pattern, including the `holder.deps is None → "server not ready"` guard for the lifespan startup/teardown window. Add both new tool closures here; the lifespan/`ToolDeps` wiring already provides `runner`/`locks`/`store`. [Source: src/dev_helper_mcp/server_factory.py:67-99]

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 1 — exactly one git path:** every git call (incl. `worktree list`, `worktree remove`, `branch -d/-D`, `rev-list`) via `GitRunner.run_git` on the correct pool. Never `subprocess`/`os.system` in `src/`. Destructive ops → `Pool.MUTATION`; list/count → `Pool.READ`.
- **Invariant 4 / derive-on-read:** `git worktree list --porcelain` is the sole truth for worktree existence; never persist derived state. The list path performs NO writes and NO destructive git (Invariant 10). An orphaned link is shown+flagged, **never auto-deleted, never auto-`done`** on the read/derive path.
- **Invariant 10 — destructive git never on the read/refresh path; two distinct removal guards:** `force` (dirty/locked worktree → `worktree remove --force`) and `force_unmerged_branch` (unmerged branch → `branch -D`), unmerged-count surfaced first. [architecture.md lines 474-477]
- **Invariant 12 / AR-14 — per-`repo_path` async mutex** serializes same-repo mutations: `remove_worktree` MUST hold it; read/refresh (`list_worktrees`) MUST NOT. [architecture.md lines 444-450; epics.md:73]
- **Invariant 7 — SDK seam:** `core/worktrees.py` + `git/porcelain.py` import no `mcp`/`starlette` (auto-policed by `tests/test_adapter_seam.py`).
- **Error contract:** core raises typed `DevHelperError`; adapter converts. Unexpected → `Internal`, never a stack trace. **All JSON keys snake_case** (`repo_path`, `worktree_path`, `task_id`, `unmerged_commits`). Timestamps via `now_iso()` only. [project-context.md#Data, format & error contract]

### Critical gotchas (carry into implementation)
- **Worktree removed BEFORE branch deleted.** git refuses to delete a branch checked out in a worktree — `worktree remove` first, then `branch -d/-D`. (Same ordering as `GitRepoMutator.remove`.)
- **Two flags, never conflated.** `force` clears the *worktree* guard (dirty/locked); `force_unmerged_branch` clears the *branch* guard (unmerged). Setting one must not silently grant the other.
- **`UnmergedBranch` surfaces the count FIRST.** Compute it on the READ pool (`rev-list --count <branch> --not --all`) before raising; put it in `details` (the "what would be lost" preview). [architecture.md lines 190-191]
- **`branch -d` (safe), not `-d`-vs-`-D` confusion:** non-forced branch delete is `-d` (refuses unmerged); forced is `-D`. The non-forced worktree remove has NO `--force`; forced adds `--force`.
- **`run_git` RETURNS non-zero; it does not raise it.** Classify `DirtyWorktree`/`LockedWorktree`/`UnmergedBranch` from `GitResult.returncode`+`stderr` yourself. Match stderr defensively (English substrings: `"contains modified or untracked files"`/`"use --force"` → DirtyWorktree; `"is locked"` → LockedWorktree; `"not fully merged"` → UnmergedBranch). _(Note: matches English stderr; same locale-fragility caveat logged in deferred-work.md#story-1-4 applies — degrade to `Internal` if unrecognized.)_
- **AC3 "nothing changes":** on a `DirtyWorktree`/`LockedWorktree` guard, do NOT delete the link row, do NOT delete the branch, do NOT touch the task — re-raise the typed error from inside the still-held mutex, release in `finally`.
- **Orphaned links on the LIST path are data, not garbage:** show+flag, never delete. Deleting a link is ONLY the explicit `remove_worktree` mutation's job.
- **asyncio objects per loop.** Core takes the injected `runner`/`locks`/`store`; build everything inside the `asyncio.run()` body in tests (no `pytest-asyncio`).

### 🛑 Git safety in tests — HARD RULE, gate-enforced (read before writing any test)
Story 1.5's tests run **real destructive git** (`worktree remove [--force]`, `branch -d/-D`). A test pointing these at a path resolving to THIS repo mutates this working tree — the incident that once destroyed `master`. Enforced by two guards (landed `8178e7a`):
- **`tests/conftest.py` autouse `_guard_project_repo_untouched`** — snapshots refs/HEAD, asserts byte-identical after every test.
- **`tests/test_git_safety.py`** — AST-scans the test tree; every git subprocess MUST pass `-C <repo>` AND `env=` (GIT_*-stripped); `os.system`/`os.popen` to git forbidden.
Therefore: all git targets `tmp_git_repo`/`tmp_path` repos only; use the `tmp_git_repo` fixture (real repo, one commit on `main`, GIT_*-stripped env, `-C` targeted — `tests/conftest.py`). The real-teardown precedent already in the suite: `test_done_status_allows_retask` (real `worktree remove --force` + `branch -D` on tmp) and 1.4's `GitRepoMutator` real-git tests — mirror them. [Source: project-context.md#Git safety in tests; tests/conftest.py; tests/test_git_safety.py]

### Previous-story (1.4) intelligence that applies directly
- **The `core/mutator.py` seam exists and is flagged for 1.5:** its module docstring says "Story 1.5 may extend it with the user-facing list/remove surface, or move the seam — either is in-spirit." **Decision for this story:** create the dedicated `core/worktrees.py` (architecture's named home) for the guarded list/remove; leave `GitRepoMutator` as the rollback-only force-teardown primitive (see the deferred-finding note above). Do not break create_task's rollback by retrofitting guards into `GitRepoMutator`.
- **Test style proven across 1.2–1.4:** unit-test core directly with injected `GitRunner()`/`RepoLockRegistry()`/tmp-file `Store` + `tmp_git_repo`; drive with `asyncio.run()`; assert outcomes on the filesystem (`git worktree list`) AND the DB. No `pytest-asyncio`, no new dep, no `slow` test. [Source: 1-3/1-4 stories §Testing]
- **Classification-from-stderr precedent:** 1.4's `_classify_add_failure` (`core/mutator.py`) is the pattern for mapping `GitResult.stderr` → typed error; mirror its defensive substring approach (and its `Internal` fallback) for the remove guards. [Source: src/dev_helper_mcp/core/mutator.py]
- **Persist-AFTER-git discipline (reinforced by 1.4's review):** do the destructive git first, then mutate the store, all inside the held mutex — so a git failure leaves no stale rows. (1.4 review moved persist inside the compensation try for exactly this reason.) [Source: 1-4 story §Review Findings]

### Git / recent-work intelligence
- **Baseline commit `8178e7a`** ("project git tree from unit tests fix flaking timing test"). 1.4 (the rollback seam + `core/mutator.py`) is implemented and reviewed-`done` on top of it (working-tree changes: `core/mutator.py`, `core/tasks.py`, `tests/test_tasks.py` + the 3 review patches). 1.5 builds on that substrate.
- **Src tree:** `core/mutator.py`, `core/tasks.py`, `core/slug.py`, `git/runner.py`, `git/repo_lock.py`, `store.py`, `tools/{handlers,models}.py`, `server_factory.py` all exist. **No `git/porcelain.py`, no `core/worktrees.py`** yet — 1.5 creates both. [Source: find src]

### Latest tech / version notes
- **`git worktree list --porcelain -z`** — NUL-delimited; records separated by an empty (NUL) line; attribute lines `worktree <path>`, `HEAD <sha>`, `branch <ref>`, and bare keywords `detached`/`bare`/`locked`/`prunable`. `-z` makes paths NUL-safe (handles spaces/unicode). [git worktree docs]
- **`git worktree remove <path>`** refuses on a dirty/locked worktree; `--force` overrides. **`git branch -d <branch>`** refuses an unmerged branch; `-D` force-deletes. **`git rev-list --count <branch> --not --all`** counts commits unique to `<branch>` (the unmerged preview). [git docs]
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`, `src/` layout. `typing.Protocol`/dataclass for the parser DTO; type hints on every public signature. [project-context.md#Technology Stack]
- **`mcp` 1.28.x FastMCP** — a tool returning a plain `dict` serializes as JSON **text** content (`content[0].text`); `structuredContent` stays `None`. In-process ASGI base URL must be `http://127.0.0.1:<port>`; `ASGITransport` does NOT auto-run the lifespan (wrap in `async with app.router.lifespan_context(app)`). [project-context.md#Testing rules]

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/git/porcelain.py` (pure `--porcelain -z` parser, SDK-free, I/O-free) and `src/dev_helper_mcp/core/worktrees.py` (SDK-free core: `list_worktrees` live-git×store join + guarded `remove_worktree`).
- **UPDATE:** `store.py` (+`list_worktree_links`, +`delete_worktree`; reuse `count_worktrees`/`delete_task`); `tools/models.py` (+`ListWorktreesIn`, +`RemoveWorktreeIn`); `tools/handlers.py` (+`list_worktrees`, +`remove_worktree` adapters); `server_factory.py` (+ two `@mcp.tool()` closures). `tests/`: new `test_porcelain.py`, `test_worktrees.py`; extend `test_concurrency.py`/`test_tools.py`.
- **UNCHANGED (do not edit):** `core/mutator.py` (`GitRepoMutator` stays rollback-only), `core/tasks.py` (create/rollback), `git/runner.py`, `git/repo_lock.py`, `errors.py` (taxonomy complete). **The DB schema is unchanged** — no migration.
- **DEFERRED, do NOT create:** `cache.py`, `projection.py`, `/state` endpoint, dashboard, `update_task`/`list_tasks`, `add_worktree`, any reconciliation sweep. [Source: architecture.md#Directory Structure; project-context.md; deferred-work.md]
- Worktrees are repo siblings (`<repo>.worktrees/<slug>`); runtime DB at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db` — never under `src/` or the project repo.

### Testing standards
- `tests/` mirrors `src/`; `test_<module>.py`; async via `asyncio.run()` (no `pytest-asyncio`); reuse `tmp_git_repo` (2–3 instances for the multi-repo cases). Unit-test `core.worktrees` directly with injected `GitRunner()`/`RepoLockRegistry()`/tmp-file `Store`.
- **Porcelain parser:** static `-z` byte-fixture corpus (detached/locked/prunable/unicode) — pure unit tests, no git, no safety surface.
- **Assert removal outcomes on filesystem + DB:** after a remove, `git -C <tmp_repo> worktree list` no longer shows the path, the dir is gone, the `task_worktree` row is dropped, sibling rows intact; on a guard, everything is still present. Use the `tmp_git_repo`-style `-C`+`env`-stripped helper for probes (git-safety gate).
- Everything green under the enforced gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dep, no `slow` test. [Source: project-context.md#Testing rules, #Git safety in tests; 1-3/1-4 stories]

### References
- [Source: epics.md#Story 1.5 (lines 263-292)] — user story + all 5 ACs verbatim; `list_worktrees(repo?, task_id?)`, `remove_worktree` two-guard semantics.
- [Source: epics.md:33-34] — FR-2 (live listing, not a cache) + FR-3 (one-worktree removal, two guards, unmerged-count-first).
- [Source: epics.md:64-73] — AR-5 (`run_git` pools, `-z`), AR-6 (two-table store + cascade), AR-8 (error taxonomy), AR-14 (per-repo mutex applies to `remove_worktree`), Epic 1 Risk ACs.
- [Source: architecture.md lines 422-477] — tool surface (5), force-flag semantics (two distinct guards), error taxonomy verbatim, read vs mutation pools.
- [Source: architecture.md lines 444-450 / Invariant 12] — per-repo mutation mutex; read/refresh ops do not take it.
- [Source: architecture.md lines 320-348] — store schema, `ON DELETE CASCADE`, the orphaned-link "shown/flagged, never auto-deleted, never auto-`done`" rule.
- [Source: architecture.md lines 730-748] — `git/porcelain.py` + `core/worktrees.py` placement; porcelain fixture corpus / test files.
- [Source: project-context.md] — SDK seam, one-git-path, derive-on-read, snake_case/error contract, git-safety-in-tests; "this file wins over architecture pseudo-code" (cache is Epic 2).
- [Source: deferred-work.md#story-1-4] — the deferred unguarded-`branch -D` finding this story resolves (keep `GitRepoMutator` rollback-only; build guarded remove separately).
- [Source: src/dev_helper_mcp/{git/runner.py, git/repo_lock.py, store.py, config.py, errors.py, tools/handlers.py, tools/models.py, server_factory.py, core/mutator.py}] — substrate APIs + the exact patterns to mirror.
- [Source: 1-4-create-task-cross-repo-rollback-error-safe.md] — seam reuse note, classification-from-stderr precedent, persist-after-git discipline, test style.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- Empirically verified `git worktree list --porcelain -z` is unsupported on the dev machine (git 2.34.1; `-z` for `worktree list` landed in git 2.36) → `error: unknown switch 'z'`. Operator decision (2026-06-23): drop `-z`, parse the newline-delimited `--porcelain` form (parser is delimiter-agnostic, also accepts the NUL `-z` form for forward-robustness).
- Empirically verified the story's suggested `git rev-list --count <branch> --not --all` returns **0** for the unmerged preview (because `--all` includes `<branch>` itself). Fixed: enumerate every ref via `for-each-ref` EXCEPT the target branch and use that as the negative set (`rev-list --count <branch> --not <other-refs…>`) → correct count.
- Discovered the AC4 retry path: on `UnmergedBranch` the worktree is already removed but the DB link is intentionally kept; a retry with `force_unmerged_branch=true` must not fail re-removing the gone worktree. `remove_worktree` now treats `"is not a working tree"` (rc 128) as idempotent-success and falls through to branch deletion + persistence.

### Completion Notes List

- **All 5 ACs satisfied.** Added two MCP tools (`list_worktrees`, `remove_worktree`) + the pure `git/porcelain.py` parser, all in `core/`/`git/` (SDK-free — `test_adapter_seam.py` green). 25 new tests; full gate green (`ruff check` + `ruff format --check` + `pytest -m "not slow"` → 129 passed; slow smoke still green). No new dependency, no `slow`-marked test, no schema change.
- **AC1 (live list):** `core.worktrees.list_worktrees` reads stored `task_worktree` links (Store `list_worktree_links` join), then fans out `git worktree list --porcelain` per repo on the READ pool and LEFT-JOINs on branch — **live derive, not a cache** (Epic 2's `cache.py`/`projection.py` deliberately not pulled forward). Orphaned links (branch gone) are returned flagged `orphaned: true`, never auto-deleted/auto-`done`.
- **⚠️ AC1 `-z` deviation (operator-approved):** epics AC1 quotes `git worktree list --porcelain -z`, but the dev git (2.34.1) errors on `-z` for `worktree list` (added in git 2.36), which would break the pre-commit gate. Per operator decision (2026-06-23) we invoke `--porcelain` **without** `-z`; the parser is delimiter-agnostic (auto-detects NUL vs newline) so a future `-z` switch needs no parser change. AC1's binding requirement ("not a stale cache") is fully met. Worktree paths we generate (`<repo>.worktrees/<slug>`) are newline-free, so the `-z` path-safety benefit is marginal here. **Recommend** the reviewer either (a) require git ≥ 2.36 and re-enable `-z`, or (b) update the epics AC text to drop `-z`.
- **AC2/3/4 (guarded remove):** `remove_worktree` holds the per-repo mutex, runs the *safe* `git worktree remove` (→ `DirtyWorktree`/`LockedWorktree` unless `force=true`) then, on `delete_branch=true`, the safe `git branch -d` (→ `UnmergedBranch` with `details.unmerged_commits` surfaced FIRST, unless `force_unmerged_branch=true`). Two flags, two distinct blast radii — never conflated. On a guard, nothing changes (rows/branch intact). Persistence is LAST (after git succeeds), inside the held mutex.
- **AC5 (last worktree → task closed):** after dropping the link, `if count_worktrees(task_id) == 0: delete_task(task_id)` — the `task` row is deleted (the settled "closed/detached" semantics). Status is never mutated here (Story 1.6 owns the four-state lifecycle). Sibling repos' worktrees/rows untouched.
- **Deferred 1.4 finding resolved:** `GitRepoMutator` left unchanged (rollback-only force-teardown); the user-facing guarded remove is a separate code path in `core/worktrees.py`. Two call sites, two blast radii — not unified.
- **Concurrency (AR-14):** `test_concurrency.py` proves `remove_worktree` is serialized by the per-repo mutex (a held same-repo lock blocks it deterministically) and that `list_worktrees` does NOT take the mutex (completes under a held lock).

### File List

**New (src):**
- `src/dev_helper_mcp/git/porcelain.py` — pure, SDK-free, I/O-free `git worktree list --porcelain[ -z]` parser (`WorktreeEntry`, `parse_worktree_porcelain`).
- `src/dev_helper_mcp/core/worktrees.py` — SDK-free core: `list_worktrees` (live-git × store join) + guarded `remove_worktree` (two guards, two flags, AC5 task-close).

**Modified (src):**
- `src/dev_helper_mcp/store.py` — added `list_worktree_links(repo?, task_id?)` (read join) and `delete_worktree(task_id, repo_path)` (drop one link). No schema change.
- `src/dev_helper_mcp/tools/models.py` — added `ListWorktreesIn`, `RemoveWorktreeIn`.
- `src/dev_helper_mcp/tools/handlers.py` — added `list_worktrees`, `remove_worktree` envelope adapters.
- `src/dev_helper_mcp/server_factory.py` — registered the two new `@mcp.tool()` closures; updated `build_mcp` docstring.

**New (tests):**
- `tests/test_porcelain.py` — 9 pure-parser fixture tests (newline + NUL forms; detached/locked/prunable/bare/unicode/space/empty/no-trailing-blank).

**Modified (tests):**
- `tests/test_worktrees.py` — 10 tests (AC1 list join/filters/orphaned/empty + AC2-5 remove guards/force/unmerged/last-worktree/TaskNotFound). _(New file this story; listed as modified because a linter reformatted it post-creation.)_
- `tests/test_concurrency.py` — added the AR-14 `remove_worktree` serialization test + `list_worktrees`-takes-no-mutex test.
- `tests/test_handlers.py` — added `list_worktrees`/`remove_worktree` envelope + typed-error tests.
- `tests/test_server_factory.py` — added the `list_worktrees`/`remove_worktree` MCP round-trip registration test.

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-23 | Story 1.5 implemented: `list_worktrees` + guarded `remove_worktree` MCP tools + `git/porcelain.py` parser. All 5 ACs satisfied; 25 new tests; full gate green. AC1 invoked without `-z` (operator-approved; git 2.34 lacks `worktree list -z`) — parser is delimiter-agnostic. |
| 2026-06-23 | Code review (3-layer adversarial): all 5 ACs confirmed satisfied. Applied 5 patches — `worktree_already_gone` flag on orphan cleanup; branch-already-gone treated as idempotent (no link wedge); `list_worktrees` degrades on a raised `GitTimeout`/`Internal` per repo; `_unmerged_commit_count` uses full refname (option-injection safe); empty-string `repo` filter treated as no-filter. 4 new tests (133 total green). 8 findings deferred (logged to deferred-work.md), 3 dismissed. Status → done. |
