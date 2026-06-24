---
baseline_commit: 8178e7a
---

# Story 1.4: `create_task` cross-repo rollback (error-safe)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent,
I want a partially-failed `create_task` to leave every repo and the store exactly as before the call,
so that a failure never leaves orphaned worktrees, branches, or task records.

## Acceptance Criteria

1. **Post-preflight failure → reverse-order compensation, no rows (AR-13).**
   **Given** repos `[A,B,C]` where worktree creation in `C` fails (e.g. a post-preflight race surfacing as `BranchExists`/`WorktreePathInUse`, a disk error, or an injected fault),
   **When** `create_task` runs,
   **Then** the worktrees already created in `A` and `B` are torn down — `git worktree remove --force <path>` **and** `git branch -D agent/<task>` — in **reverse creation order** (`B` then `A`);
   **And** no `task` / `task_worktree` rows persist (the DB looks like the call never happened);
   **And** the original failure cause is the error returned to the caller (a typed `DevHelperError`), not swallowed.

2. **Preflight failure → nothing started (cheapest rollback).**
   **Given** a preflight failure (a repo invalid / colliding before any mutation: `NotAGitRepo`, `BranchExists`, `WorktreePathInUse`, `BaseRefNotFound`, `InvalidTaskName`, `ActiveTaskConflict`),
   **When** `create_task` runs,
   **Then** nothing is created in any repo and no compensation runs — the create never started. *(This is the Story 1.3 behavior; 1.4 must preserve it unchanged — it is the cheapest rollback.)*

3. **Compensation that itself fails → `RollbackIncomplete`, original cause preserved.**
   **Given** a compensating teardown that itself fails (e.g. `worktree remove`/`branch -D` returns non-zero for one already-created repo),
   **When** rollback runs,
   **Then** `RollbackIncomplete` is returned whose `details` lists the **repo paths left orphaned**, **and** preserves the **original cause** (the failure that triggered the rollback) as the reason;
   **And** nothing is swallowed — repos that *were* successfully torn down are not reported as orphaned, and still no DB rows persist.

4. **A clean rollback leaves no residue — retry succeeds.**
   **Given** a create that rolled back cleanly (AC-1),
   **When** `create_task` is retried with the **same** `task_name` and repo set,
   **Then** it succeeds — no residual slug record, no `agent/<task>` branch, and no worktree directory collide in any repo.

5. **`RepoMutator` seam makes the partial-failure matrix deterministic.**
   **Given** the worktree create/teardown primitives sit behind an injectable `RepoMutator` seam,
   **When** tests inject a `FlakyMutator` that fails the `add` of a chosen repo (and, for AC-3, also fails a `remove` of a chosen repo),
   **Then** the partial-failure matrix — fail on repo `i` of `N` for `i ∈ {1, 2, N}`, `N ∈ {1, 2, 3}` — is **deterministic** and asserts **zero worktrees, zero branches, zero rows** after a clean rollback (AC-1/4), and exactly the expected orphan set under a compensation failure (AC-3).

*Note (carry verbatim into the story file's scope):* crash-safety (SIGKILL/OOM mid-`create_task`) is an **explicit v1 non-goal**. The residue of a crash is a no-DB-row orphan worktree, surfaced later by derive-on-read (Epic 2) and re-colliding (`BranchExists`) on a same-name retry — visible and recoverable, never silent corruption. **Do NOT build a startup reconciliation sweep.**

## Tasks / Subtasks

> **Build order** (each step independently testable): introduce the `RepoMutator` seam → refactor `core/tasks.create` provisioning to call `mutator.add` and track provisioned repos → add the reverse-order compensation path (`mutator.remove`) with `RollbackIncomplete` on teardown failure → best-effort typed classification of the triggering `add` failure → `FlakyMutator` test double → partial-failure matrix tests. **Persistence and preflight are unchanged from 1.3 — do not touch the single-transaction `persist_created_task` or the preflight gates except to keep them intact.**

> **Scope fence:** post-preflight compensation + `RollbackIncomplete` + the `RepoMutator` fault-injection seam ONLY. **No** `list_worktrees`/`remove_worktree` tool (Story 1.5), **no** `cache.py`/`projection.py`/dashboard (Epic 2), **no** startup reconciliation engine (v1 non-goal), **no** crash-safety mechanism.

- [x] **Task 1 — Introduce the `RepoMutator` seam (AC: 1, 3, 5)**
  - [x] Create a small SDK-free seam in the **core layer** (recommended: `src/dev_helper_mcp/core/mutator.py`; alternatively seed `core/worktrees.py` for Story 1.5 to extend — flag the choice for the reviewer). It MUST NOT import `mcp`/`starlette` (policed by `tests/test_adapter_seam.py`, which scans `core/`). **Chose `core/mutator.py`** (flagged in module docstring for the reviewer; Story 1.5 may extend or relocate it). SDK-seam test stays green.
  - [x] Define the seam interface (a `Protocol` or a small ABC) — two async methods, both repo-targeted and idempotent-friendly:
    - `async def add(self, repo: str, branch: str, worktree_path: str, start_point: str) -> None` — creates branch+worktree via the **mutation** pool (`git worktree add -b <branch> <worktree_path> <start_point> --`); raises a typed `DevHelperError` on non-zero exit (see Task 4 for classification).
    - `async def remove(self, repo: str, branch: str, worktree_path: str) -> None` — compensation primitive: `git worktree remove --force <worktree_path>` then `git branch -D <branch>`, both via the **mutation** pool; raises a typed error if either git op returns non-zero (so the orchestrator can detect a failed teardown for AC-3). Implemented as a `typing.Protocol`.
  - [x] Real implementation `GitRepoMutator` wraps the injected `GitRunner` (it holds `runner`; it does NOT construct one). All git goes through `runner.run_git(repo, [...], pool=Pool.MUTATION)` — **never** `subprocess`/`os.system` (Invariant 1). The destructive ops (`worktree remove --force`, `branch -D`) run on the **mutation** pool while the per-repo mutex is held — they are NEVER on the read/refresh path (project-context anti-pattern).
  - [x] Move the current inline `git worktree add -b …` call (`core/tasks.py:120-135`) into `GitRepoMutator.add`. The `add` happy-path behavior must stay byte-for-byte equivalent (same command, same `--` end-of-options, same MUTATION pool) so Story 1.3's green tests keep passing. Verified: all 13 pre-existing `test_tasks.py` tests still pass unchanged.

- [x] **Task 2 — Wire the mutator into `core.tasks.create` via DI (AC: 1, 5)**
  - [x] Add an optional keyword param: `async def create(..., runner, locks, store, mutator: RepoMutator | None = None)`. When `mutator is None`, default to `GitRepoMutator(runner)` **inside** the function (so production callers and existing tests need no change; only fault-injection tests pass a `FlakyMutator`).
  - [x] The adapter (`tools/handlers.py`) is **unchanged** — it keeps calling `core.tasks.create(...)` without `mutator`; the default covers production. (Confirm no signature break: `mutator` is keyword-only with a default.) Confirmed — `handlers.py` untouched; full suite green.
  - [x] Preflight, slug gate, per-repo mutex acquisition (sorted-abspath order), and the single-transaction `persist_created_task` (rows last) all stay exactly as in 1.3.

- [x] **Task 3 — Reverse-order compensation path (AC: 1, 3, 4)**
  - [x] In the provisioning loop, replace the inline `worktree add` with `await mutator.add(repo, branch, str(wt_path), start_point)` and append `(repo, branch, str(wt_path))` to a `provisioned` list **only after `add` returns** (so the failing repo is NOT in `provisioned`).
  - [x] Wrap provisioning in `try/except DevHelperError as cause:`. On any `add` failure:
    1. Iterate `reversed(provisioned)` and call `await mutator.remove(repo, branch, wt_path)` for each, collecting `(repo, exception)` for any teardown that raises.
    2. **Persist no rows** (the persist call is only reached on full success — keep it after the loop, outside the except).
    3. If **all** compensations succeeded → **re-raise `cause`** (the original typed error; preserves the real reason — AC-1).
    4. If **any** compensation failed → raise `RollbackIncomplete("compensating teardown failed", {...})` with `details` containing: `orphaned_repos` (the repo paths whose teardown failed — and therefore may still have a worktree/branch), `original_cause` = `cause.as_dict()` (or `{code, message, details}`), and optionally `compensation_errors`. Use `raise RollbackIncomplete(...) from cause` to chain. **Never swallow** `cause` (AC-3). Implemented with `compensation_errors` included.
  - [x] Compensation runs **while the per-repo mutexes are still held** (inside the existing `try` whose `finally` releases them) — do not release locks before rolling back.
  - [x] Order matters: teardown is the **reverse** of creation order (Story asserts `B` then `A` for `[A,B,C]` failing at `C`). Asserted via `FlakyMutator.remove_calls` ordering in the matrix + AC-3 tests.

- [x] **Task 4 — Best-effort typed classification of the triggering `add` failure (AC: 1)**
  - [x] In `GitRepoMutator.add`, when `git worktree add` returns non-zero, classify by `stderr` where unambiguous: `"already exists"`/`"is already checked out"` for the branch → `BranchExists`; an existing target path → `WorktreePathInUse`; otherwise `Internal` carrying the trimmed `stderr` (`result.stderr.decode(errors="replace").strip()`). This closes the 1.3 deferral's "surfaces collisions as generic `Internal`" note. **Do not over-engineer** — an `Internal` with stderr is an acceptable fallback; the *compensation* is the hard requirement, the typed code is the nice-to-have. Implemented in `_classify_add_failure` (branch markers `"a branch named"`/`"is already checked out"` → `BranchExists`; `"already exists"` → `WorktreePathInUse`; else `Internal`); covered by two real-git classification tests.
  - [x] Whatever typed error `add` raises becomes `cause` and flows to the caller per AC-1 (or is preserved inside `RollbackIncomplete` per AC-3).

- [x] **Task 5 — `FlakyMutator` test double + partial-failure matrix (AC: 1, 3, 4, 5)**
  - [x] In the test module, define `FlakyMutator` that **wraps a real `GitRepoMutator`** so non-failing repos do real git (real worktrees/branches are created and really torn down → filesystem-assertable), and the targeted `(repo, phase)` raises a synthetic typed error instead of calling git. Recommended shape: `FlakyMutator(inner, *, fail_add_on: str | None = None, fail_remove_on: str | None = None)` keyed by **absolute repo path** (matches the canonical sorted-abspath the orchestrator uses). Honor the AC-5 spirit of `fail_on_repo=i, fail_on_phase={add|remove}` — supporting both an add-trigger and a remove-failure is required for the AC-3 test (you need an `add` failure to *start* rollback and a `remove` failure *during* it). Implemented with `add_calls`/`remove_calls` recording for order assertions.
  - [x] **AC-1 / AC-5 matrix** (`tests/test_tasks.py`, extend): for `N ∈ {1,2,3}` build `N` `tmp_git_repo`s; for `i ∈ {1,2,N}` inject `fail_add_on=<i-th canonical repo>`; assert after the call raises the injected cause: **zero** `agent/<slug>` branches in **every** repo, **zero** worktree dirs (`worktree_path_for`), and `store.get_task(slug) is None`. Assert teardown reverse order if observable (e.g. via the wrapping mutator recording calls). Done as a parametrized test (positions clamped to valid `1..N`); reverse order asserted.
  - [x] **AC-3** (RollbackIncomplete): inject `fail_add_on=<repo C>` **and** `fail_remove_on=<repo A or B>`; assert the raised error is `RollbackIncomplete`, `details["orphaned_repos"]` lists exactly the repo(s) whose teardown failed, `details["original_cause"]` carries the triggering code, and `store.get_task(slug) is None`. Also asserts the successfully-torn-down repo has no residue while the orphan retains its worktree/branch, and `__cause__` is chained.
  - [x] **AC-4** (clean retry): after a clean rolled-back create (`fail_add_on` set), drop the flaky mutator and call `create_task` again with the same `task_name`/repos using the real mutator → succeeds; assert branch+worktree+rows now exist. (Proves no residual slug/branch/dir collision.)
  - [x] **Preserve AC-2:** keep/extend the 1.3 preflight-no-mutation tests (a preflight reject must NOT enter provisioning and must NOT call `mutator.remove`). A spy mutator asserting `add`/`remove` were never called on a preflight reject is a clean way to prove "never started." Added `SpyMutator` test; the 1.3 preflight-no-mutation tests are retained unchanged.

- [x] **Task 6 — Gate green + git-safety compliance (AC: all)**
  - [x] All new git in tests targets `tmp_git_repo`/`tmp_path` repos **only** — see **Git safety in tests** in Dev Notes (this is non-negotiable and now gate-enforced). Real teardown git (`worktree remove --force`, `branch -D`) runs against the tmp repos the `tmp_git_repo` fixture creates, never the project repo. `tests/test_git_safety.py` + the autouse project-repo guard both pass.
  - [x] Full gate green: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. Add **no** `slow`-marked test, **no** new dependency. Result: ruff clean, 33 files formatted, **101 passed, 1 deselected**. No new dep, no slow test.

### Review Findings

_Adversarial code review 2026-06-23 (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Triage: 1 decision-needed, 2 patch, 3 defer, 5 dismissed. All 5 ACs + binding invariants verified satisfied by the Acceptance Auditor; findings below concern the story's user-story *intent* and robustness, not the literal ACs._

- [x] [Review][Patch] **Persist-phase failure leaks all worktrees + branches (no compensation)** [src/dev_helper_mcp/core/tasks.py:171-182] — _FIXED 2026-06-23: `persist_created_task` moved inside the compensation `try`; a persist failure now runs the same reverse-order teardown. Test `test_persist_failure_rolls_back_worktrees` added._ — `store.persist_created_task` raises `ActiveTaskConflict` (TOCTOU PK clash) or `Internal` (disk-full / db-locked) but is called *outside* the provisioning `try/except` (which closes at line 169). If it fires after all `add`s succeed, every worktree + `agent/<slug>` branch is left on disk with **no rollback and no DB row** — directly defeating the story's user-story guarantee ("leave every repo *and the store* exactly as before") and AR-13 atomicity. Independently flagged Critical by Blind + Edge hunters; the Acceptance Auditor passed it because it sits outside the 5 literal ACs. _Decision (2026-06-23): patch now in 1.4 — extend reverse-order compensation to the persist window + add a regression test._

- [x] [Review][Patch] **`test_git_mutator_remove_raises_on_failure` can't tell which git op failed** [tests/test_tasks.py] — `remove` runs `worktree remove --force` then `branch -D`, each raising a distinct `Internal` (`"git worktree remove failed"` vs `"git branch -D failed"`); the test asserted only the generic `Internal`. _FIXED 2026-06-23: split into `..._on_worktree_failure` (asserts message + `worktree_path` detail) and `..._on_branch_failure` (worktree removed first, then `branch -D` fails — asserts message + `branch` detail)._
- [x] [Review][Patch] **`_classify_add_failure` has no direct unit test** [tests/test_tasks.py / src/dev_helper_mcp/core/mutator.py:528-546] — the most regression-prone logic (stderr substring matching, branch-before-path precedence, `Internal` fallback) was only exercised via two real-git happy cases. _FIXED 2026-06-23: `test_classify_add_failure_precedence_and_fallback` covers branch precedence (incl. "is already checked out"), path collision, and the `Internal` fallback carrying trimmed stderr._

- [x] [Review][Defer] **`except DevHelperError` doesn't catch `CancelledError`/`BaseException`** [src/dev_helper_mcp/core/tasks.py:143] — a task cancellation (shutdown/client disconnect) mid-`add` bypasses compensation entirely, leaking already-provisioned worktrees. Adjacent to the explicit v1 crash-safety non-goal; a correct fix needs `asyncio.shield` around teardown. Deferred — hardening, non-trivial.
- [x] [Review][Defer] **`_classify_add_failure` is locale-fragile** [src/dev_helper_mcp/core/mutator.py:528-546 / git/runner.py:132] — the runner env strips git-context vars + sets `GIT_TERMINAL_PROMPT`/`GIT_OPTIONAL_LOCKS` but does NOT pin `LC_ALL=C`, so non-English git stderr makes classification fall back to `Internal`. Degrades safely (compensation still runs); fix edits `runner.py` (out of this story's scope). Deferred.
- [x] [Review][Defer] **`remove`'s `branch -D` force-deletes unguarded at the reusable seam** [src/dev_helper_mcp/core/mutator.py:600] — safe today (branch was just created by this call), but the module docstring anticipates Story 1.5 reusing the seam for user-facing `remove_worktree`, where force-deleting an unmerged pre-existing branch would be data loss. Guard/document the teardown-only contract before reuse. Deferred to Story 1.5.

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
Story 1.3 shipped the happy path + **preflight** rejection: every error in 1.3's ACs is raised *before any git mutation*, so success and rejection both leave the system clean with no compensation. **Story 1.4 is exactly the missing half: what happens when a git mutation fails *after* preflight passed.** In scope:
- The **`RepoMutator` seam** (injectable create/teardown primitives) for deterministic fault injection.
- **Reverse-order compensation** (`worktree remove --force` + `branch -D`) of already-created repos on a post-preflight `add` failure.
- **`RollbackIncomplete`** when a compensation itself fails (orphans named, original cause preserved).
- Best-effort **typed classification** of the triggering `add` failure (closes a 1.3 review defer).

Explicitly **OUT of scope** (do not pull forward):
- **`list_worktrees` / `remove_worktree` tools** — Story 1.5. (You add a *core* teardown primitive for compensation; you do NOT add the user-facing removal tool, its two force-flag guards `DirtyWorktree`/`UnmergedBranch`, or `git/porcelain.py`.)
- **`update_task` / `list_tasks`** — Story 1.6.
- **`cache.py` / `projection.py` / dashboard** — Epic 2. Do NOT call any `cache.refresh()` (the module does not exist).
- **Startup reconciliation / crash-safety** — explicit v1 non-goal (see the Note under ACs). No sweep, no `link.health`, no persisted orphan state.
- **Multi-process / cross-process mutual exclusion** — the per-repo mutex is in-process only; the cross-process gap is mitigated by the machine-global single-instance lock (Story 3.1), not here. (Deferred item below.)

### What the substrate already gives you (verified shipped in 1.1/1.2/1.3 — reconcile against THIS, not the architecture's pseudo-code)
- **`core/tasks.py` → `async def create(task_name, description, repos, *, base_ref=None, runner, locks, store) -> dict`** — the orchestrator you are extending. Current shape: acquire per-repo mutexes in **sorted-abspath order** (release reversed in `finally`) → active-slug gate (`status != "done"`) → per-repo preflight (raise on first collision) → **provisioning loop** (the part 1.4 rewrites) → `store.persist_created_task(...)` **last** → return `data`. [Source: src/dev_helper_mcp/core/tasks.py]
- **`git/runner.py` → `GitRunner.run_git(repo, args, *, pool) -> GitResult`** — `Pool.READ` (3s, sem=2) / `Pool.MUTATION` (~120s, sem=4). **Non-zero exits are RETURNED, not raised** (`GitResult(returncode, stdout: bytes, stderr: bytes)`); only `GitTimeout`/`NotAGitRepo`/`Internal` are raised. Compensation git uses `Pool.MUTATION`. `stdout`/`stderr` are **bytes** — `.decode(errors="replace")`. [Source: src/dev_helper_mcp/git/runner.py]
- **`errors.py`** — `RollbackIncomplete` **already exists** (`code = "RollbackIncomplete"`); you only *raise* it. `DevHelperError.as_dict() -> {code, message, details}`. Also `Internal`, `BranchExists`, `WorktreePathInUse` available for classification. **Do not add new error classes** — the taxonomy is complete and stable contract. [Source: src/dev_helper_mcp/errors.py]
- **`config.py`** — `branch_name_for(slug) -> "agent/<slug>"`, `worktree_path_for(repo: Path, slug) -> <repo>.worktrees/<slug>` (sibling of the repo). Pools/timeouts already there. **Append** any new tunable here (project-context: no magic numbers in modules) — but 1.4 likely needs none. [Source: src/dev_helper_mcp/config.py]
- **`store.py` → `persist_created_task(...)`** — single transaction, rows written last, retask UPSERT, `IntegrityError → ActiveTaskConflict`. **Unchanged in 1.4.** It is only called on full success (after all `add`s succeed), so a rollback path simply never reaches it → "no rows" is automatic. [Source: src/dev_helper_mcp/store.py]
- **`tools/handlers.py` → `create_task` handler** — wraps `core.tasks.create` in the `{ok, data, error}` envelope; `RollbackIncomplete` (a `DevHelperError`) is converted to `{ok:false, error:{code:"RollbackIncomplete", message, details}}` automatically — **no handler change needed**. [Source: src/dev_helper_mcp/tools/handlers.py]
- **`tmp_git_repo` fixture** (`tests/conftest.py`) — real repo, one commit on branch `main`, `GIT_*`-stripped env, `-C` targeted. Instantiate 2–3 for the multi-repo matrix. **This fixture IS the "empty sub-git repo for testing git operations"** the project mandates — use it; never touch the project repo. [Source: tests/conftest.py]

### 🛑 Git safety in tests — HARD RULE, now gate-enforced (read before writing any test)
Story 1.4's tests run **real destructive git** (`git worktree remove --force`, `git branch -D`) during compensation. A test that points these at a path resolving to the project's own working tree mutates **this** repo — the incident that once destroyed branch `master`. This is now a stated rule **and** enforced by two guards added in `8178e7a`:
- **`tests/conftest.py` autouse `_guard_project_repo_untouched`** — snapshots the project repo's refs/HEAD and asserts they are byte-identical after **every** test. If your test mutates the project repo, it fails loudly (pinpointing the offender).
- **`tests/test_git_safety.py`** — AST-scans the whole test tree; every git subprocess MUST pass `-C <repo>` (never default to CWD = project repo) **and** `env=` (so inherited `GIT_*` is stripped). `os.system`/`os.popen` to git is forbidden. A non-compliant test fails the gate.

**Therefore, for 1.4 tests:**
- All git targets `tmp_git_repo`/`tmp_path` repos **only**. Real teardown happens on the tmp worktrees the fixture created.
- Make `FlakyMutator` fail **in-process** (raise a synthetic typed error), NOT by corrupting git state — so compensation runs **real** git on **real tmp** worktrees deterministically, and the failure injection itself touches nothing on disk.
- If you add any direct `subprocess` git in a test helper, copy the `tmp_git_repo` pattern exactly (`["git", "-C", str(tmp_repo), …]`, `env=` GIT_*-stripped) or `test_git_safety.py` will fail.
[Source: project-context.md#Git safety in tests; tests/conftest.py; tests/test_git_safety.py]

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 1 — exactly one git path:** every git call (including compensation `worktree remove`/`branch -D`) via `GitRunner.run_git` on the correct pool. The destructive ops use `Pool.MUTATION`. Never `subprocess`/`os.system` for git in `src/`. [Source: architecture.md#Invariants; project-context.md#Async & git discipline]
- **Invariant 12 — per-`repo_path` async mutex** serializes same-repo mutations; acquire in **sorted-abspath order**, release reversed. **Compensation must run while these locks are still held** (inside the existing `try`, before `finally` releases them) — releasing early would let a concurrent create see a half-torn-down repo. [Source: architecture.md#Per-repo mutation mutex]
- **Invariant 4 — derive-on-read / never persist derived state:** rollback writes NO rows and removes NO DB state beyond never-writing-them; do not add an "orphan" column or any reconciliation persistence. [Source: architecture.md#Data Architecture]
- **Invariant 6 — no blocking call on the event loop:** all git off-loop via `run_git`; `os.path.*` is fine. No `subprocess.run`. [Source: architecture.md#Invariants]
- **Invariant 7 — SDK seam:** `core/mutator.py` (or `core/worktrees.py`) and `core/tasks.py` import **no** `mcp`/`starlette` (auto-policed by `tests/test_adapter_seam.py`, which scans `core/`+`git/`+`store.py`). [Source: tests/test_adapter_seam.py; project-context.md#SDK-isolation seam]
- **Error contract:** core raises typed `DevHelperError`; `RollbackIncomplete` carries `{code, message, details}` with snake_case detail keys; the adapter converts. Unexpected exception → `Internal`, never a stack trace. **All JSON keys snake_case** (`orphaned_repos`, `original_cause`). [Source: project-context.md#Data, format & error contract]

### Critical gotchas (carry into implementation)
- **The failing repo is NOT in `provisioned`.** Append to `provisioned` only *after* `mutator.add` returns. Otherwise compensation will try to remove a worktree that was never created → spurious teardown failure → false `RollbackIncomplete`.
- **Compensation order is reverse-of-creation.** `[A,B,C]` failing at `C` → tear down `B`, then `A`. Tests assert this.
- **`branch -D`, not `-d`.** The `agent/<task>` branch is brand-new and unmerged; `-d` (safe delete) would refuse it. `-D` (force) is correct here — and is safe because the branch was created by this very call. [Source: architecture.md#create_task atomicity & rollback]
- **`worktree remove --force`** handles the just-created (clean, empty) worktree robustly. After removing the worktree, delete the branch — order: worktree first (a checked-out branch can't be deleted), then `branch -D`.
- **Re-raise the ORIGINAL cause on clean rollback (AC-1), not a wrapper.** Only escalate to `RollbackIncomplete` when a *compensation* fails (AC-3). Don't blanket-wrap every rollback in `RollbackIncomplete`.
- **Do NOT release the per-repo mutexes before compensating** — the `finally: lock.release()` block runs after the whole try (including rollback). Keep rollback inside the try.
- **Persist stays last and only on success.** Do not move `persist_created_task` into the try-before-add or you'll write rows you then have to delete. The current ordering already gives "no rows on failure" for free.
- **`run_git` returns non-zero; it does not raise it.** In `GitRepoMutator.add`/`remove`, classify by `GitResult.returncode`/`stderr` and raise the typed error yourself. [Source: 1-3 Dev Notes; src/dev_helper_mcp/git/runner.py]
- **asyncio objects per loop.** `GitRepoMutator` holds the injected `runner`; do not construct `GitRunner`/`Store`/`RepoLockRegistry` at import time. In tests, build everything inside the `asyncio.run()` body (no `pytest-asyncio`). [Source: 1-2/1-3 Dev Notes]

### Previous-story (1.3) intelligence that applies directly
- **The exact code to rewrite** is `core/tasks.py:116-136` — the provisioning loop that currently raises bare `Internal` on the first failed `worktree add` with **no compensation**. 1.3's Dev Notes and its review explicitly deferred this to 1.4 (Review finding `[Defer]` "Post-preflight partial state"). [Source: 1-3 story §Review Findings; deferred-work.md#story-1-3]
- **AC-4 (`status != 'done'`) and AC-5 preflight are settled and tested** — don't regress them. The per-repo mutex sorted-abspath ordering is the #1 multi-repo correctness trap; it's already correct — keep it. [Source: 1-3 story]
- **Test style:** unit-test `core.tasks.create` **directly** with injected `GitRunner()`/`RepoLockRegistry()`/tmp-file `Store` + `tmp_git_repo`; drive with `asyncio.run()`; **no `pytest-asyncio`**, **no new dep**, **no `slow` test**. Use a **file** DB where you assert persistence (here: assert *absence* of rows). [Source: 1-3 story §Testing standards]
- **1.3 already wrote** `test_done_status_allows_retask` which does a real `worktree remove --force` + `branch -D agent/redo` against `tmp_git_repo` (`tests/test_tasks.py:265-272`) — a working precedent for real teardown git in tests, already git-safe. Mirror that pattern. [Source: tests/test_tasks.py]

### Git / recent-work intelligence
- **Baseline commit: `8178e7a`** ("project git tree from unit tests fix flaking timing test"). Recent commits: `8178e7a` (git-safety guards + deterministic timeout test), `bf73121`, `4851e65` (1-3), `bf90594` (init). The git-safety guards (`_guard_project_repo_untouched`, `tests/test_git_safety.py`) and the project-context "Git safety in tests" section all landed in `8178e7a` — they directly govern this story's tests. [Source: git log; project-context.md]
- **Src tree:** no `core/worktrees.py`, no `core/mutator.py`, no `RepoMutator`/`FlakyMutator` exist yet — 1.4 introduces the seam. `core/tasks.py`, `git/runner.py`, `git/repo_lock.py`, `store.py`, `tools/` are all in place and reviewed. [Source: find src]

### Latest tech / version notes
- **`git worktree remove --force <path>`** removes a worktree even with untracked/modified content; safe on the just-created clean worktree. Must run **before** deleting its branch (git refuses to delete a branch checked out in a worktree). [Source: git worktree docs]
- **`git branch -D agent/<slug>`** force-deletes the (unmerged, brand-new) branch. `-d` would refuse it. [Source: git branch docs]
- **`git worktree add -b <branch> <path> <start> --`** (the `add` primitive, moved into `GitRepoMutator.add`) fails non-zero if `<branch>` exists or `<path>` is occupied — preflight normally guards both, so a non-zero here post-preflight is a genuine race/disk/fault. [Source: 1-3 Dev Notes; architecture.md#Async-git execution]
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`, `src/` layout. `Protocol` (from `typing`) is the lightweight way to define the `RepoMutator` seam without an ABC. [Source: project-context.md#Technology Stack]
- **`mcp` 1.28.x FastMCP** — a tool returning a plain `dict` serializes as JSON **text** content; if you add an ASGI round-trip asserting a `RollbackIncomplete` envelope, parse `content[0].text`. (Optional; the unit tests are primary.) [Source: project-context.md#Testing rules]

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/core/mutator.py` — `RepoMutator` seam (`Protocol` or ABC) + `GitRepoMutator(runner)`. *(Reviewer choice: may instead seed `core/worktrees.py` so Story 1.5 extends it with list/remove; either is in-spirit — flag which you chose.)* SDK-free.
- **UPDATE:** `src/dev_helper_mcp/core/tasks.py` — accept optional `mutator` (default `GitRepoMutator(runner)`); replace inline `worktree add` with `mutator.add`; add reverse-order compensation + `RollbackIncomplete`. `tests/test_tasks.py` — add the `FlakyMutator` double + partial-failure matrix + AC-3/AC-4 tests; keep all 1.3 tests green.
- **UNCHANGED (do not edit):** `store.py` (`persist_created_task`), `errors.py` (`RollbackIncomplete` already defined), `tools/handlers.py` / `tools/models.py` (envelope handles `RollbackIncomplete` automatically), `git/runner.py`, `git/repo_lock.py`.
- **DEFERRED, do NOT create:** `git/porcelain.py`, the `remove_worktree` tool surface (`DirtyWorktree`/`UnmergedBranch` guards), `cache.py`, `projection.py`, `lock.py`, any reconciliation sweep. [Source: architecture.md#Complete Project Directory Structure; deferred-work.md]
- Worktrees are repo siblings (`<repo>.worktrees/<slug>`); runtime DB stays at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db` — never under `src/` or the project repo. [Source: project-context.md#Persistence]

### Testing standards
- `tests/` mirrors `src/`; `test_<module>.py`; async via `asyncio.run()`; reuse `tmp_git_repo` (2–3 instances for the matrix). Unit-test `core.tasks.create` directly with `FlakyMutator` injected — fast, deterministic, no port, no `slow` marker.
- **Assert the rollback outcome on the filesystem + DB:** after a clean rollback, for **every** repo assert no `agent/<slug>` branch (`git -C <tmp_repo> rev-parse --verify --quiet refs/heads/agent/<slug>` → non-zero) and no `worktree_path_for(repo, slug)` dir, and `store.get_task(slug) is None`. Use the `tmp_git_repo`-style `-C` + `env`-stripped helper for these probes (git-safety gate).
- Everything green under the enforced gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. [Source: project-context.md#Testing rules, #Git safety in tests; 1-3 story]

### References
- [Source: epics.md#Story 1.4: `create_task` cross-repo rollback (error-safe)] — user story + all 5 ACs (verbatim), crash-safety non-goal note
- [Source: epics.md#Epic 1] — epic goal; story sequencing (1.4 = rollback as its own story; "Risk ACs: rollback-failure matrix, incl. failure during compensation"); AR-13/AR-14 coverage
- [Source: architecture.md#API & Communication Patterns — `create_task` atomicity & rollback (AR-13)] — preflight-first, rows-last, reverse-order compensation, `RollbackIncomplete` contract, crash-safety non-goal (lines 427-443)
- [Source: architecture.md#Per-repo mutation mutex] — Invariant 12; serialize same-repo mutations; deadlock-safe sorted ordering
- [Source: architecture.md#Error taxonomy] — `RollbackIncomplete` definition: `details` lists orphaned repo paths; original cause preserved (lines 466-473)
- [Source: architecture.md#Invariants — rule 12] — all-or-nothing; compensate on failure; `RollbackIncomplete` never silent
- [Source: src/dev_helper_mcp/core/tasks.py] — the orchestrator + the exact provisioning loop (116-136) to refactor
- [Source: src/dev_helper_mcp/git/runner.py] — `run_git`/`Pool`/`GitResult`; non-zero returned not raised; MUTATION pool for destructive ops
- [Source: src/dev_helper_mcp/errors.py] — `RollbackIncomplete`/`Internal`/`BranchExists`/`WorktreePathInUse` (already defined; only raise)
- [Source: src/dev_helper_mcp/store.py] — `persist_created_task` single-transaction (unchanged; "no rows on failure" is automatic)
- [Source: src/dev_helper_mcp/tools/handlers.py] — `{ok,data,error}` envelope (converts `RollbackIncomplete` automatically; no change)
- [Source: deferred-work.md#story-1-3] — the deferred "post-preflight partial state / no compensation" item this story closes; the process-local-mutex item it does NOT close (→ Story 3.1)
- [Source: 1-3-create-a-multi-repo-task-happy-path.md] — substrate APIs, preflight/persist structure, test style, real-teardown precedent (`test_done_status_allows_retask`)
- [Source: project-context.md#Git safety in tests] — HARD rule + the two gate-enforced guards (`_guard_project_repo_untouched`, `tests/test_git_safety.py`) governing this story's destructive-git tests
- [Source: project-context.md#Anti-patterns] — "A destructive git op (`worktree remove --force`, `branch -D`) on the read/refresh path" is forbidden; 1.4's destructive ops run on the MUTATION pool under the per-repo mutex, never read/refresh
- [Source: tests/conftest.py; tests/test_git_safety.py; tests/test_adapter_seam.py] — `tmp_git_repo` fixture; the git-safety static scan; the SDK-seam scope

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- Initial new-test run failed `test_add_failure_rolls_back_clean[1]` with `IndexError: list index out of range` — the matrix generated fail-position `2` for `N=1`. Fixed by clamping positions to valid `1..N` (`{1, 2, n} if p <= n`). Re-run: green.

### Completion Notes List

- **`RepoMutator` seam** added as `core/mutator.py` (chosen over `core/worktrees.py`; flagged for reviewer in the module docstring). A `typing.Protocol` defines `add`/`remove`; `GitRepoMutator` is the production impl that holds the injected `GitRunner` and routes both ops through `run_git(..., pool=Pool.MUTATION)` — no `subprocess`. SDK-seam scan (`test_adapter_seam.py`) stays green.
- **Typed classification** (`_classify_add_failure`): a non-zero `git worktree add` maps `"a branch named"`/`"is already checked out"` → `BranchExists`, `"already exists"` → `WorktreePathInUse`, else `Internal` carrying trimmed stderr. Closes the Story 1.3 "collisions surface as generic `Internal`" deferral. Two real-git tests assert the branch and path cases.
- **`core.tasks.create`** now takes a keyword-only `mutator: RepoMutator | None = None` (defaults to `GitRepoMutator(runner)` inside the function), so production and all 1.3 tests are unchanged. The provisioning loop calls `mutator.add` and appends to `provisioned` **only after** it returns; an `add` failure triggers reverse-order `mutator.remove` while the per-repo mutexes are still held. Clean rollback re-raises the original `cause`; a teardown that itself raises escalates to `RollbackIncomplete` with `orphaned_repos`, `original_cause`, `compensation_errors` and `raise ... from cause`. `persist_created_task` stays last and is only reached on full success → "no rows on failure" is automatic.
- **Tests** (`tests/test_tasks.py`): added `FlakyMutator` (wraps a real `GitRepoMutator`, fails a chosen `add`/`remove` in-process so compensation runs real git on real tmp worktrees) and `SpyMutator`. Coverage: AC-1/AC-5 parametrized matrix (`N∈{1,2,3}`, fail at `i∈{1,2,N}` → zero branches/worktrees/rows + reverse teardown order), AC-3 (`RollbackIncomplete` names exactly the orphaned repo, preserves the cause, chains `__cause__`, leaves the orphan's residue while the cleanly-removed repo has none), AC-4 (clean rollback → same-name retry with the real mutator succeeds), AC-2 (`SpyMutator` proves a preflight reject never calls the mutator).
- **Git-safety:** all new tests target `tmp_path`/`tmp_git_repo` repos only; `test_git_safety.py` static scan and the autouse `_guard_project_repo_untouched` both pass. No new dependency, no `slow`-marked test.
- **Gate:** `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` → ruff clean, 33 files formatted, **101 passed, 1 deselected** (the slow uvicorn smoke test).

### File List

- `src/dev_helper_mcp/core/mutator.py` — **new**: `RepoMutator` Protocol, `GitRepoMutator`, `_classify_add_failure`.
- `src/dev_helper_mcp/core/tasks.py` — **modified**: optional `mutator` DI param; provisioning via `mutator.add`; reverse-order compensation + `RollbackIncomplete`; docstring/imports updated (`Internal` import dropped, `DevHelperError`/`RollbackIncomplete`/`mutator` added).
- `tests/test_tasks.py` — **modified**: `FlakyMutator`/`SpyMutator` doubles + partial-failure matrix and AC-1/2/3/4/5 + classification tests; imports updated.

## Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-06-23 | 0.1.0 | Created Story 1.4 context — `create_task` cross-repo rollback (error-safe): `RepoMutator` seam, reverse-order compensation, `RollbackIncomplete`, `FlakyMutator` partial-failure matrix. Ready for dev. |
| 2026-06-23 | 1.0.0 | Implemented Story 1.4: `core/mutator.py` (`RepoMutator`/`GitRepoMutator` + typed `add` classification); `core.tasks.create` reverse-order compensation with `RollbackIncomplete` and original-cause preservation; `FlakyMutator`/`SpyMutator` doubles + partial-failure matrix (AC-1..5). Gate green (101 passed, 1 deselected). Status → review. |
