# Story 2.2: In-memory cache and background refresher

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want an ephemeral in-memory cache rebuilt from a per-repo git fan-out on a background tick and on every mutating tool call,
so that the dashboard reads current state cheaply and a slow or unavailable repo never blanks the board.

## Acceptance Criteria

1. **Given** active tasks spanning several repos,
   **When** the background refresher ticks,
   **Then** it reads each **distinct** repo's `git worktree list --porcelain` via the **READ pool**, runs the Story 2.1 `project(...)`, and **atomically swaps** the cache ref to the new immutable `CacheSnapshot` (last-writer-wins on the ref; a single GIL-atomic assignment, never an in-place mutation).

2. **Given** a mutating tool call (`create_task` / `remove_worktree` / `update_task`),
   **When** it completes successfully,
   **Then** the cache is refreshed **before the tool returns**, so a tool never returns `ok` on stale git-derived state. (The tool's existing return `data` is unchanged — the refresh is a side effect on the shared cache, not a change to the envelope payload.)

3. **Given** git is unavailable or a repo's read times out,
   **When** a refresh runs,
   **Then** the cache keeps **last-known** state and is marked **stale** rather than going blank;
   **And** a single slow/timed-out repo degrades **that repo only** — its worktrees render last-known/unavailable (surfaced via a `repo_unavailable:<repo_path>` warning) while the rest of the snapshot builds normally from fresh reads.

4. **Given** ≤ 15 tracked repos,
   **When** the fan-out perf/chaos test runs (parametrized `num_tasks` × `repos_per_task`, a slow-repo injector via `asyncio.sleep`, concurrent readers exercising the 2s pool-acquire),
   **Then** p95 derive latency stays within the ≤3s soft SLO;
   **And** the test documents the latency cliff beyond 15 repos (the SLO is explicitly bounded, not a hard guarantee).

## ⛔ HARD PREREQUISITE — read before anything else

**Story 2.2 cannot be implemented until Story 2.1 is implemented.** 2.1 (`ready-for-dev`, not yet `done`) ships `src/dev_helper_mcp/projection.py` with the pure `project(*, git_listings, tasks, generated_at) -> CacheSnapshot` function and the three frozen dataclasses (`CacheSnapshot`, `TaskView`, `WorktreeView`). This story **consumes** that function and that shape — it does **not** define them.

- If `projection.py` does not exist when you start, **implement Story 2.1 first** (`_bmad-output/implementation-artifacts/2-1-derive-on-read-projection-pure-task-grouped.md`), get its gate green, then return here.
- Treat 2.1's `project()` signature and `CacheSnapshot`/`TaskView`/`WorktreeView` field set as a **fixed contract**. This story adds **zero** fields to them and does **not** change `project()`'s signature. (Story 2.1 Decision B: `generated_at` is injected by the caller — that caller is THIS story's `cache.refresh()`, stamping `now_iso()`.)

## Tasks / Subtasks

- [ ] **Task 1 — Add the two tunables to `config.py`** (AC: 1, 4)
  - [ ] `CACHE_REFRESH_INTERVAL: float = 2.0` — background tick period in seconds (**operator-confirmed 2026-06-25, Decision C**). Default 2.0s keeps worst-case staleness under the ≤3s freshness SLO (FR-9 / UX-DR6). Comment it as the tick period (distinct from the dashboard *poll* interval, which is a 2.4b UI concern).
  - [ ] No other config change. The READ-pool timeouts/sizes (`GIT_READ_TIMEOUT=3.0`, `GIT_READ_POOL_SIZE=2`, `GIT_READ_ACQUIRE_TIMEOUT=2.0`) already exist (config.py:34-39) and are reused as-is — they ARE the per-repo degrade mechanism.
- [ ] **Task 2 — Create `src/dev_helper_mcp/cache.py` (NEW, core/SDK-free)** (AC: 1, 3)
  - [ ] Module docstring: ephemeral in-memory derive-on-read cache; the **only** writer of the in-memory view (architecture.md:765-781). Imports only stdlib (`asyncio`, `dataclasses`, `logging`, `collections.abc`), `git/runner` (`GitRunner`, `Pool`), `git/porcelain` (`parse_worktree_porcelain`, `WorktreeEntry`), `store` (`Store`), `projection` (`project`, `CacheSnapshot`), `config`, `util.now_iso`, `errors.DevHelperError`. **NO `mcp`/`starlette`** (auto-policed — `cache.py` is already in `tests/test_adapter_seam.py:19`'s `SEAM_MODULES`).
  - [ ] `class Cache:` holds the injected `GitRunner` + `Store` (constructed in the lifespan, loop-bound — Cache does NOT construct them, per the "asyncio objects live in the running loop" rule), plus two private fields: `_current: CacheSnapshot` (seeded to an empty snapshot `CacheSnapshot(generated_at=now_iso(), tasks=(), warnings=())`) and `_last_listings: dict[str, list[WorktreeEntry]]` (per-repo last-successful porcelain, `{}` initially).
  - [ ] `@property current(self) -> CacheSnapshot:` returns `self._current` **by reference, no lock** (the read path; `/state` (2.3) and read tools read this — a single ref read is atomic; a swap can never produce a torn snapshot because the snapshot is frozen and replaced whole).
  - [ ] `async def refresh(self) -> None:` — the core rebuild. **Total — it never raises** (mirror the projection's totality one layer up; a mutating tool calls it after a committed mutation and must not fail because git is flaky). Algorithm:
    1. `try:` read `tasks = await self._store.list_tasks()` (no filter — full snapshot). On any exception → log a warning and **return without swapping** (keep last-known; AC3 "never blank"). This is the "store unreadable / git fully unavailable upstream" guard.
    2. Compute the distinct repo set: `repos = sorted({wt["repo_path"] for t in tasks for wt in t["worktrees"]})`.
    3. **Concurrent per-repo fan-out on the READ pool**, each repo degrading independently (reuse the proven pattern from `core/worktrees.list_worktrees:69-100`): `results = await asyncio.gather(*[self._read_repo(r) for r in repos])` where `_read_repo` returns `(repo_path, list[WorktreeEntry] | None)` and **never raises** (catches `DevHelperError` from `run_git` — `GitTimeout`/`Internal` — AND a non-zero `returncode`, returning `None` on either). The READ pool's `semaphore=2` throttles concurrency; the `2s acquire timeout` fails a contended repo fast (→ `None`).
    4. Build `git_listings: dict[str, list[WorktreeEntry]]` and `unavailable: list[str]`: for each `(repo, entries)` — if `entries is not None`: `git_listings[repo] = entries` **and** `self._last_listings[repo] = entries` (record last-known); else: `unavailable.append(repo)` and **carry forward** `self._last_listings.get(repo)` into `git_listings` if present (so that repo's worktrees still render last-known instead of flipping to orphaned).
    5. `snapshot = project(git_listings=git_listings, tasks=tasks, generated_at=now_iso())` — the pure 2.1 join. (Reads committed DB state, so the just-committed mutation IS reflected — AC2.)
    6. **Merge degrade warnings without changing the shape:** if `unavailable`: `snapshot = dataclasses.replace(snapshot, warnings=tuple(sorted(snapshot.warnings + tuple(f"repo_unavailable:{r}" for r in unavailable))))`. (`project()` produces `orphan_link:` warnings; the cache adds `repo_unavailable:` — the projection cannot know a read *failed*, only the cache does.)
    7. **Stale / don't-swap rule (AC3):** if `repos` is non-empty **and every** repo was unavailable (`len(unavailable) == len(repos)` and `len(repos) > 0`) → **do not swap**; keep `self._current` so its `generated_at` ages and the UI (2.4c) labels the whole board stale/last-known. Log a warning. Otherwise (all-ok, partial-degrade, or genuinely zero repos) → **swap**: `self._current = snapshot` (single assignment, GIL-atomic).
  - [ ] `async def _read_repo(self, repo_path: str) -> tuple[str, list[WorktreeEntry] | None]:` — one `run_git(repo_path, ["worktree", "list", "--porcelain"], pool=Pool.READ)`; on `DevHelperError` (raised: `GitTimeout`, `Internal`) → log + return `(repo_path, None)`; on `returncode != 0` (returned: vanished/not-a-repo) → log + return `(repo_path, None)`; else `(repo_path, parse_worktree_porcelain(result.stdout))`. **Do NOT use `-z`** — git 2.34 on this machine errors on `worktree list -z`; the non-`-z` form + the delimiter-agnostic parser is the established choice (porcelain.py:10-13, deferred-work.md AC1-`-z`).
- [ ] **Task 3 — Background refresher loop in `cache.py`** (AC: 1)
  - [ ] `async def run_refresher(cache: Cache, *, interval: float) -> None:` — `while True: try: await cache.refresh(); except asyncio.CancelledError: raise (re-raise so shutdown cancels cleanly); except Exception: logger.exception(...) (a bad tick must never kill the loop — though refresh() is already total, belt-and-suspenders); await asyncio.sleep(interval)`. SDK-free (lives in core `cache.py`); the adapter owns only the `create_task`/`cancel` lifecycle (Task 5).
- [ ] **Task 4 — Wire the cache into `ToolDeps` and refresh after every mutation** (AC: 2)
  - [ ] `tools/handlers.py`: add `cache: Cache` to the `ToolDeps` dataclass.
  - [ ] In the three **mutating** handlers (`create_task`, `remove_worktree`, `update_task`): after the core call returns the success `data` and **before** `return {"ok": True, ...}`, call `await deps.cache.refresh()`. Placement: inside the `try`, after the `data = await ...` line. Because `refresh()` is total it cannot turn a successful mutation into a failure; it only updates the shared cache so `/state` (and the next read) reflects this mutation (AC2). **Do NOT** call refresh in the two **read** handlers (`list_worktrees`, `list_tasks`) — they don't mutate state and `list_worktrees` is itself the live-derive path.
  - [ ] Keep each handler's returned `data` **exactly as today** — the architecture's "return the just-built snapshot" (step 7) is satisfied for our tools by the existing per-tool `data` dicts; we are NOT switching tool returns to the snapshot. (`/state`, Story 2.3, is the snapshot consumer.)
- [ ] **Task 5 — Wire construction + the background task into the lifespan** (AC: 1, 2)
  - [ ] `server_factory.py` `create_app.lifespan`: after opening the `Store`, build the `Cache(runner=..., store=store)` using the **same** `GitRunner` instance the `ToolDeps` get (one runner per app — its pools are the shared concurrency limiter). Order: `runner = GitRunner(); store = await Store.open(); cache = Cache(runner=runner, store=store); holder.deps = ToolDeps(runner=runner, locks=RepoLockRegistry(), store=store, cache=cache)`.
  - [ ] Do one **initial** `await cache.refresh()` before launching the loop (so `/state` is warm the instant the server is up, not blank-until-first-tick).
  - [ ] Launch the refresher: `refresher = asyncio.create_task(run_refresher(cache, interval=CACHE_REFRESH_INTERVAL))`. On shutdown (the `finally`): `refresher.cancel()` then `await` it inside a `contextlib.suppress(asyncio.CancelledError)` (or try/except CancelledError) so teardown is clean and zombie-free — BEFORE `await store.close()` (the loop touches the store; cancel it first).
  - [ ] This is the ONLY `mcp`/`starlette`-importing change in the story (server_factory is adapter). Add NO new route — the cache is in-memory; `/state` is Story 2.3. (Note: deferred-work.md flags that `Mount("/", app=mcp_app)` shadows future routes — that is a **2.3** problem, explicitly NOT 2.2's.)
- [ ] **Task 6 — Tests: `tests/test_cache.py`** (AC: 1, 2, 3)
  - [ ] **Degrade / stale / carry-forward (AC3) with a FAKE runner — no real git** (fastest, deterministic, zero git-safety surface): inject a stub runner exposing `async def run_git(self, repo, args, *, pool)` that returns a canned `GitResult` for healthy repos and raises `GitTimeout` (or returns `returncode=1`) for a chosen "slow" repo. Assert: (a) a partial-degrade refresh swaps a fresh snapshot whose `warnings` contain exactly `repo_unavailable:<repo>` for the failed repo, and the failed repo's worktrees still render (carried-forward last-known, not orphaned) after a prior successful tick; (b) an **all-repos-unavailable** refresh does NOT advance `current` (same object / same `generated_at` as before — last-known kept); (c) `refresh()` never raises on any failure injected. Pair with a fake/`:memory:` Store (or a tiny stub returning a fixed `list_tasks()` shape).
  - [ ] **Atomic swap + by-ref read (AC1):** after a successful `refresh()`, `cache.current` is a new `CacheSnapshot` instance (identity changed) with the projected tasks; two reads of `current` return the same object until the next swap.
  - [ ] **End-to-end refresh over real git (AC1)** using the `tmp_git_repo` fixture + a real `GitRunner` + a `:memory:`/tmp `Store`: create a task's worktree (via `core.tasks.create` against the tmp repo, OR seed Store rows + a real `git worktree add` in the tmp repo), `await cache.refresh()`, assert the snapshot groups it correctly (delegates the join correctness to 2.1's tests; here just prove the fan-out → project → swap pipeline works end-to-end on real porcelain). **Git-safety HARD RULE applies** — every git op targets `tmp_git_repo`, never the project repo; the autouse `_guard_project_repo_untouched` + `test_git_safety.py` AST scan enforce it.
  - [ ] **Critical-section refresh (AC2)** — drive the in-process ASGI client (`asgi_client_factory`) through the lifespan: call `create_task` against a `tmp_git_repo`, then read `holder.deps.cache.current` (or, once 2.3 lands, `/state`) and assert the new task is present — i.e. the mutation refreshed the cache before returning. (If wiring the ASGI client to a tmp repo is heavy, an equivalent lower-level test: construct `ToolDeps` with a real cache, call `handlers.create_task(...)`, assert `deps.cache.current` reflects it.) Remember `ASGITransport` does NOT auto-run the lifespan — wrap in `async with app.router.lifespan_context(app):` (project-context.md#Testing).
- [ ] **Task 7 — Perf/chaos test (AC4), `@pytest.mark.slow`** in `tests/test_cache.py` (or `tests/test_cache_perf.py`)
  - [ ] Parametrize `num_tasks` × `repos_per_task` up to ~15 distinct repos. Build the listings via a **fake runner** with an injected `asyncio.sleep(slow_delay)` on one chosen repo (the "slow-repo injector") so the test is deterministic and does NOT need 15 real git repos. Exercise concurrent readers of `cache.current` during a refresh; assert no torn read and that p95 `refresh()` wall-time stays within the ≤3s soft SLO for ≤15 repos. **`log`/assert-document the cliff beyond 15 repos** (e.g. a comment + an explicit assertion that the SLO is bounded, not guaranteed past 15) — do NOT silently cap.
  - [ ] Mark `@pytest.mark.slow` (the `slow` marker is registered) so the default gate (`pytest -m "not slow"`) skips it; it runs in the full suite. Time via injected delays / `asyncio` timing, never `Date.now()`-style wall clock assertions that flake.
- [ ] **Task 8 — Gate green + seam confirmation** (AC: all)
  - [ ] `cache.py` adds no `from mcp …`/`from starlette …` — `tests/test_adapter_seam.py` (already scans `cache.py`) stays green.
  - [ ] Full gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new runtime dependency (stdlib `asyncio` + existing `aiosqlite`/git). No schema change, no migration, no new HTTP route.

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
2.2 builds the **derive-on-read cache + its background refresher + the post-mutation refresh hook**. It is the second Epic 2 story; it stacks directly on 2.1's pure `project()`.

- **BUILD:** `src/dev_helper_mcp/cache.py` (NEW: `Cache` class + `run_refresher`), two `config.py` constants, `ToolDeps.cache` + the post-mutation `await deps.cache.refresh()` in the three mutating handlers, and the lifespan wiring (build cache, warm it, launch+cancel the refresher task). Tests in `tests/test_cache.py` (+ a `slow` perf test).
- **DO NOT BUILD (later stories — hard fence):**
  - **No `/state` route, no JSON serialization of the snapshot, no HTTP endpoint** → **Story 2.3**. (2.2 only fills the in-memory cache; nothing reads it over HTTP yet.)
  - **No dashboard, no HTML/JS, no diff-and-patch render, no freshness/empty-state UI** → **Stories 2.4a/b/c**.
  - **No change to 2.1's `projection.py`** — do not add fields to `CacheSnapshot`/`WorktreeView`, do not change `project()`'s signature. Degrade is surfaced via `warnings` + aging `generated_at` (see Decision B), precisely so 2.1's pinned shape stays frozen.
  - **No new git command and no destructive git on the refresh path** — only `git worktree list --porcelain` on the READ pool (Invariant 1 / 10). The refresher NEVER prunes, removes, or `branch -D`s an orphan (Invariant 4: surface, never auto-clean).
  - **No orphan reconciliation / cleanup sweep** — derive-on-read *reports*; cleanup is a v1 non-goal (architecture.md:439-443).
  - **No `-z`** porcelain (git 2.34 compatibility — deferred-work.md AC1-`-z`).
- [Source: epics.md:355-379 (this story); epics.md:381-401 (2.3 owns `/state`); epics.md:404-474 (2.4a/b/c own UI); architecture.md:741-742, 765-781; deferred-work.md]

### ✅ Decision A — WHERE the post-mutation refresh lives (OPERATOR-CONFIRMED 2026-06-25: adapter-level)
The architecture pins a single "mutation critical-section ordering" (architecture.md:393-406): `acquire mutation slot → run_git mutation → UPSERT → rebuild snapshot → swap ref → release slot → return`, with the rebuild **inside** the critical section. But our per-repo mutex + mutation pool live **inside core** (`core/tasks.create`, `core/worktrees.remove_worktree`), which are SDK-free, cache-free, and **already reviewed-`done`**. Two ways to honor "never return `ok` on stale state":
- **Recommended (the story is written for this) — refresh in the adapter handler, after core returns `ok`, before the envelope.** The cache is an adapter-lifecycle object (built in the lifespan, served by `/state` which is adapter); the three mutating handlers call `await deps.cache.refresh()` after the core mutation commits. **Correctness:** the architecture's own justification — "because each rebuild is a full snapshot, last-writer-wins on the ref is correct (no partial merge)" (architecture.md:405-406) — does NOT depend on holding the per-repo mutex. `refresh()` reads **committed** DB state + live git, so it always reflects the just-committed mutation; concurrent refreshes race harmlessly (last full-snapshot writer wins; never torn). **Big win: zero edit to the reviewed-`done` Epic 1 core mutation paths** (no regression surface), and `update_task` (which takes no runner/mutex today) needs no new core dependency.
- **Alternative (literal) — thread `cache` into core and refresh inside the mutex.** Higher fidelity to the pseudo-code's literal placement, but couples three core functions to the cache, forces `core.update_task` to gain a runner+cache, and edits reviewed-`done` code for no correctness gain (per the full-snapshot argument above).
- **DECIDED (operator-confirmed):** adapter-level refresh. Core mutation paths (`core/tasks.create`, `core/worktrees.remove_worktree`, `core.update_task`) stay untouched; the 3 mutating **handlers** call `await deps.cache.refresh()` after the core call returns `ok`. Do not thread `cache` into core.

### ✅ Decision B — how per-repo degrade + staleness are surfaced WITHOUT changing 2.1's shape (OPERATOR-CONFIRMED 2026-06-25)
The pinned `CacheSnapshot` has exactly `generated_at`, `tasks`, `warnings` — **no `stale` boolean, no per-repo `unavailable` field** (architecture.md:362-385; locked by Story 2.1). Yet AC3 + UX-DR8 require "git-unavailable labeled last-known (never blank)" and "per-repo degrade → only its lines unavailable". The model that satisfies both without touching 2.1's shape:
- **Per-repo unavailable → `warnings` entry `repo_unavailable:<repo_path>`** (pinned format), added by the **cache** post-projection via `dataclasses.replace(snapshot, warnings=…)`. The projection only knows present-vs-absent in `git_listings`; only the cache knows a read *failed*. The UI (2.4c) reads these warnings to render that repo's lines as "unavailable/last-known" instead of "orphaned".
- **Carry-forward last-known per-repo listings:** the `Cache` retains `_last_listings[repo] = list[WorktreeEntry]` from each successful read; on a failed read it re-feeds that repo's last-known entries into `git_listings`, so the projection emits those worktrees as **present** (last-known) rather than flipping them to `orphaned:true` (which would be semantically wrong for a transient timeout, and would fire the orphan disclosure UI). If there is no last-known yet (first tick failed), the repo is simply omitted (projection orphans its links) but the `repo_unavailable:` warning still tells the UI it's unavailable-not-orphaned.
- **Whole-board stale → don't swap.** If **every** read failed this tick (and there were repos to read), the cache keeps `self._current` untouched so its `generated_at` ages; the UI's freshness logic (UX-DR6/7, Story 2.4c) labels the board stale/last-known. A **partial** degrade DOES swap (the healthy majority is genuinely fresh; the degraded repos carry `repo_unavailable:` warnings). So: all-ok → swap fresh; partial → swap fresh + per-repo warnings; total-fail → keep last-known (age it). The single `generated_at` is the **global** freshness signal; `warnings` are the **per-repo** signal — consistent and shape-preserving.
- Alternative (rejected): omit failed repos from `git_listings` and let them read `orphaned`. Wrong — conflates transient-unavailable with genuinely-deleted, and triggers the orphan UI for a slow repo.

### ✅ Decision C — tick interval (OPERATOR-CONFIRMED 2026-06-25)
`CACHE_REFRESH_INTERVAL` default **2.0s** keeps worst-case background staleness < the ≤3s freshness SLO (FR-9). It is the *background tick*, distinct from the dashboard *poll* interval (a 2.4b concern). Configurable via the constant.

### The refresh algorithm (the heart of the story)
```
refresh():
  try:
    tasks = await store.list_tasks()          # committed DB state; total snapshot
  except Exception:
    log; return                               # keep last-known (AC3 never blank)
  repos = sorted({wt.repo_path for t in tasks for wt in t.worktrees})
  results = await gather(_read_repo(r) for r in repos)   # READ pool sem=2 throttles; each total
  git_listings, unavailable = {}, []
  for repo, entries in results:
    if entries is not None:
      git_listings[repo] = entries; _last_listings[repo] = entries
    else:
      unavailable.append(repo)
      if repo in _last_listings: git_listings[repo] = _last_listings[repo]   # carry-forward
  snap = project(git_listings=git_listings, tasks=tasks, generated_at=now_iso())   # 2.1 pure join
  if unavailable:
    snap = replace(snap, warnings=tuple(sorted(snap.warnings + (f"repo_unavailable:{r}" for r))))
  if repos and len(unavailable) == len(repos):
    log; return                               # total fail → keep last-known, let generated_at age
  self._current = snap                        # GIL-atomic swap
```
- **Distinct-repo dedup** matters: a 3-repo task and a 2-repo task sharing a repo → that repo is read **once** (the fan-out is keyed by repo, not by task-link).
- **`gather` + READ-pool semaphore is the concurrency design.** `asyncio.gather` schedules all reads, but `GitRunner`'s `_read_sem` (size 2) admits only 2 at a time and the 2s acquire-timeout fails contended repos fast (→ `None` → unavailable). Healthy porcelain reads are ~tens of ms, so ≤15 repos finish well under 3s; the 3s figure is the per-command *timeout* (worst case per hung repo), not typical latency. This is exactly what AC4's chaos test probes.

### What the substrate already gives you (verified shipped 1.1–1.6 — reconcile against THIS, not the architecture pseudo-code)
- **`core/worktrees.list_worktrees` (worktrees.py:45-112)** — the **template to copy** for the per-repo fan-out + degrade: it already loops distinct repos, runs `["worktree", "list", "--porcelain"]` on `Pool.READ`, catches `DevHelperError` AND `returncode != 0`, logs, and degrades that repo to "no live worktrees" rather than raising. 2.2's `_read_repo` is the same shape but returns `WorktreeEntry` lists (for `project()`) instead of a branch-set, and distinguishes failure (`None`) from empty (`[]`).
- **`GitRunner.run_git(repo, args, *, pool=Pool.READ)` (git/runner.py:84-99)** — raises `GitTimeout` on acquire/command timeout (subprocess killed+reaped), `Internal` if the git binary is unrunnable; **returns** a non-zero `GitResult` otherwise. The READ pool (`sem=2`, `read_timeout=3s`, `acquire_timeout=2s`) is constructed in the lifespan and shared. **One `GitRunner` per app** (its semaphores are loop-bound — never construct a second). Cache reuses the SAME runner the `ToolDeps` hold.
- **`parse_worktree_porcelain(raw: bytes) -> list[WorktreeEntry]` (git/porcelain.py:44-71)** — total parser; `branch` is `refs/heads/`-stripped and `None` on detached HEAD; accepts `-z` or newline form. Feed `result.stdout` straight in.
- **`store.list_tasks(status=None, repo=None)` (store.py:286-328)** — returns `[{task_id, description, status, created_at, updated_at, worktrees:[{repo_path, branch, worktree_path}, …]}]`, grouped+sorted. **This exact shape is `project()`'s `tasks` argument** (Story 2.1 wrote `project()` against it). Call with no filter for the full snapshot. There is **no dedicated "distinct repos" Store method** — derive the repo set from this output (`{wt.repo_path …}`); do **not** add a Store method (keeps scope tight, no schema/SQL change).
- **`util.now_iso()` (util.py:6-14)** — the single timestamp helper; the cache stamps `generated_at` with it (Story 2.1 Decision B: the projection does NOT call the clock — the *caller* does, and that caller is `cache.refresh()`).
- **`config.py` READ-pool tunables (config.py:30-44)** — already exist; reuse. Only ADD `CACHE_REFRESH_INTERVAL` (Decision C).
- **Lifespan deps pattern (server_factory.py:194-220)** — `_DepsHolder` + `ToolDeps` are built inside the running loop and nulled on teardown; tool closures guard `holder.deps is None` (startup/teardown window). The cache slots into this exact pattern; the refresher task is created/cancelled in the same `lifespan`.
- **`tests/test_adapter_seam.py:19`** already lists `cache.py` in `SEAM_MODULES` — the seam is guarded the moment the file exists. Keep it `mcp`/`starlette`-free.
- **`tmp_git_repo` fixture + autouse guards (conftest.py:142-172, :66-84, :95-105)** — `tmp_git_repo` (init + one commit, `GIT_*`-stripped env), `_guard_project_repo_untouched` (asserts the project repo's refs/HEAD unchanged after each test), `_isolate_state_dir` (redirects `XDG_STATE_HOME` so lifespan-opened Stores hit a tmp DB). Real-git tests for 2.2 use these.

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 4 — Derive-on-read; never persist derived state.** The cache is **ephemeral, in-memory** — rebuilt from scratch on restart; no derived state in SQLite. The refresher *recomputes*; it never writes the view back. [architecture.md:68, 350-360]
- **Invariant 7 — SDK seam:** `cache.py` imports no `mcp`/`starlette` (auto-policed). The refresher **loop** is also SDK-free (core); only the `asyncio.create_task`/`cancel` lifecycle is adapter (server_factory). [project-context.md#SDK-isolation seam]
- **Invariant 1 / 10 — single `run_git()`, READ pool, no destructive op on the read/refresh path.** The fan-out is `worktree list --porcelain` only, on `Pool.READ`. NEVER `worktree remove`/`branch -D`/`prune` on this path (anti-pattern; architecture.md:98). [architecture.md:65, 74; project-context.md#Async & git discipline]
- **Invariant 3 — snake_case keys.** The cache changes no keys; the snapshot's field names (set by 2.1) are already the snake_case `/state` contract. `repo_unavailable:<repo_path>` warnings are snake_case-consistent note strings. [architecture.md:67]
- **Invariant 11 — timestamps via `now_iso()` only** — `generated_at` is stamped by `cache.refresh()`. [architecture.md:75]
- **Snapshot is immutable, swapped whole** (never mutated in place) — frozen `CacheSnapshot`; `_current = snap` is the only mutation, a single GIL-atomic ref assignment. [architecture.md:390-391, 399, 405-406]

### Critical gotchas (carry into implementation)
- **`refresh()` MUST be total (never raises).** A mutating handler calls it after a committed git mutation; if `refresh()` raised, the tool would report failure for a mutation that actually succeeded — corrupting the contract. Catch everything inside `refresh()`/`_read_repo`; worst case keep last-known. This is the cache-layer analogue of 2.1's projection totality.
- **One `GitRunner` per app, shared by ToolDeps AND the Cache.** Constructing a second `GitRunner` gives a second pair of semaphores → the READ-pool concurrency cap (and the ≤3s SLO reasoning) is no longer global. Build the runner once in the lifespan and hand the same instance to both `ToolDeps` and `Cache`.
- **Cancel the refresher BEFORE closing the Store.** The loop's `refresh()` reads the store; if you `store.close()` first, an in-flight tick hits a closed connection. Order: `refresher.cancel()` → `await` (suppress `CancelledError`) → `store.close()`.
- **`asyncio.CancelledError` must propagate out of the loop.** In `run_refresher`, re-raise `CancelledError` (don't swallow it in the broad `except Exception` — `CancelledError` is a `BaseException` in 3.8+, so `except Exception` won't catch it, but be explicit: catch `CancelledError` first and `raise`). Otherwise shutdown hangs.
- **Carry-forward, not orphan, on transient failure.** Re-feeding `_last_listings[repo]` keeps that repo's worktrees rendering as present/last-known; omitting it makes the projection mark them `orphaned:true` — wrong signal (Decision B). Only emit `orphan_link:` (from `project()`) for genuinely absent branches in a **successfully-read** repo.
- **Don't swap on total failure; DO swap on partial.** The all-unavailable guard is what produces "stale, not blank". A partial degrade is still fresh for the healthy repos — swap it so their state updates.
- **Empty is valid, not stale.** Zero tasks → `repos == []` → `len(unavailable)==len(repos)==0` is **false** for the total-fail guard (guard requires `repos` non-empty) → swap an empty snapshot `CacheSnapshot(now, (), ())`. A brand-new server with no tasks shows an empty board, not a stale one.
- **Determinism for tests:** `repos` is `sorted(...)`; warnings are `sorted(...)` after the merge (2.1 already sorts `project()`'s warnings, but the merged set must be re-sorted). Avoids flaky assertions.
- **No `-z`.** `worktree list -z` errors on git 2.34 (this machine). Use `--porcelain` only; the parser is delimiter-agnostic. [porcelain.py:10-13; deferred-work.md AC1-`-z`]
- **`dataclasses.replace` on a frozen dataclass returns a NEW instance** — exactly what we want (the snapshot stays immutable; we never mutate `snap.warnings`).

### 🛑 Git safety in tests — HARD RULE (2.2 DOES spawn git, unlike 2.1)
Unlike 2.1 (pure, no git), 2.2's end-to-end and perf paths spawn real git, so the repo-wide HARD RULE is live: **every git op targets a `tmp_path` repo via `tmp_git_repo`, never this project repo.** The autouse `_guard_project_repo_untouched` (conftest.py:66-84) asserts the project repo's refs/HEAD are byte-identical after each test, and `test_git_safety.py` AST-scans for git-against-cwd. Prefer the **fake-runner** tests (Task 6 degrade, Task 7 perf) — they spawn NO git at all (a stub `run_git` returns canned `GitResult`s / sleeps), so they have zero git-safety surface and are deterministic. Reserve real git (`tmp_git_repo` + real `GitRunner`) for the one or two end-to-end pipeline tests. Never invoke `create_task`/`worktree add` against a real-world path; always `-C <tmp_repo>`.

### Previous-story (2.1 / Epic 2) intelligence that applies directly
- **2.1 is the dependency, not optional context.** This story imports `project` + `CacheSnapshot` from `projection.py`. 2.1's Decision A (untracked `agent/<slug>` worktrees grouped by slug) and Decision B (`generated_at` injected) are settled upstream; 2.2 just *calls* `project()` and *stamps* `generated_at`. If 2.1 shipped with the recommended Decision A (agent-namespace filter), the cache inherits it for free — no re-decision here.
- **"This file wins over architecture pseudo-code."** Where the architecture's literal critical-section pseudo-code (refresh inside the mutex) conflicts with the shipped core boundary, the reconciled approach (Decision A: adapter-level refresh, same correctness) wins — same posture 1.6/2.1 took. [project-context.md#Usage Guidelines]
- **`done` tasks still appear in the snapshot.** `store.list_tasks()` returns `done` tasks (rows kept until last worktree removed); `project()` surfaces them with `status="done"`; the cache passes them through. The UI (2.4a) folds them into `✓ N done` — the cache does NOT filter `done`. [2.1 Dev Notes#Two coexisting "closed" semantics]
- **Test style proven 1.2–2.1:** plain `pytest`, async via `asyncio.run()` (no `pytest-asyncio`), in-process `httpx.ASGITransport` for HTTP, `:memory:`/tmp Store, exactly the `slow` marker for the heavy perf test. No new dep. [project-context.md#Testing rules]

### Git / recent-work intelligence
- **Baseline `cc6c8fe` ("1-6 complete").** Epic 1 fully implemented + reviewed-`done`; the final 5-tool surface is live. Epic 2 has exactly one prior artifact: Story 2.1 (drafted `ready-for-dev`; `projection.py` lands with it). `cache.py` does NOT exist yet — this story creates it.
- **Commit cadence:** one commit per story after a green gate + adversarial code-review. 2.2 follows suit. Files touched: NEW `src/dev_helper_mcp/cache.py` + `tests/test_cache.py`; UPDATE `config.py` (2 constants), `tools/handlers.py` (`ToolDeps.cache` + 3 refresh calls), `server_factory.py` (lifespan wiring). No `errors.py`/`store.py`/`projection.py`/`core/` change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`; `from __future__ import annotations` at the top (matches the codebase). Type hints on every public signature (`tuple[str, list[WorktreeEntry] | None]`, `dict[str, list[WorktreeEntry]]`).
- **No new runtime dependency** — stdlib `asyncio` (gather, create_task, sleep, CancelledError), `dataclasses.replace`, plus the existing `aiosqlite`/git substrate. No `mcp`/`starlette` in `cache.py`.
- **`asyncio.gather`** preserves input order in results, so zipping `repos` with `results` is safe; still, `_read_repo` returns `(repo_path, …)` so the mapping is explicit and order-independent.

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/cache.py` (top-level package module, alongside `store.py`/`projection.py` — core-by-boundary, package-root by location, per architecture.md:741). `tests/test_cache.py` (+ optional `tests/test_cache_perf.py` for the `slow` test).
- **UPDATE:** `config.py` (+`CACHE_REFRESH_INTERVAL`, comment); `tools/handlers.py` (`ToolDeps.cache: Cache` + `await deps.cache.refresh()` in the 3 mutating handlers); `server_factory.py` (build `Cache`, warm it, launch/cancel `run_refresher`, shared `GitRunner`).
- **UNCHANGED (do not edit):** `projection.py` (2.1's contract — frozen), `store.py`, all of `core/`, `git/`, `errors.py`, `util.py`, `middleware.py`, `cli.py`, `server.py`, `tools/models.py`. **DB schema unchanged — no migration.**
- **DEFERRED, do NOT create or pull forward:** `/state` route + JSON serialization (2.3); dashboard HTML/JS, diff-patch, freshness/empty-state UI (2.4a/b/c); the `Mount("/")` route-shadowing fix (2.3's concern — deferred-work.md); any orphan cleanup/reconciliation (v1 non-goal). [epics.md:381-474; architecture.md:741-742; deferred-work.md]
- Test mirrors src: `tests/test_cache.py` (the architecture's planned test file, architecture.md:757 — "cache swap atomicity, background refresh, stale-on-git-unavailable, per-repo degrade, mutation-path refresh, ≤3s fan-out perf").

### Testing standards
- `tests/test_cache.py`: plain `pytest`, async via `asyncio.run()`. **Prefer fake-runner unit tests** (degrade, stale, swap, carry-forward, totality) — fast, deterministic, no git-safety surface. Use `tmp_git_repo` + a real `GitRunner` only for the 1–2 end-to-end pipeline + critical-section tests. In-process ASGI (`asgi_client_factory`) for the mutation-refresh test, wrapped in `async with app.router.lifespan_context(app):` (ASGITransport does NOT auto-run the lifespan).
- **Coverage to the four ACs:** (1) atomic swap + by-ref read + background tick rebuilds; (2) cache reflects a `create_task`/`remove_worktree`/`update_task` mutation before the tool returns; (3) per-repo degrade → `repo_unavailable:<repo>` warning + carried-forward last-known (not orphaned), total-fail → last-known kept (no swap, `generated_at` ages), and `refresh()` never raises; (4) `@pytest.mark.slow` perf/chaos — parametrized `num_tasks`×`repos_per_task`, slow-repo `asyncio.sleep` injector, concurrent readers, p95 ≤3s for ≤15 repos, documented cliff beyond.
- Green under the enforced gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dep, no schema migration. `tests/test_adapter_seam.py` (scans `cache.py`) stays green.

### References
- [Source: epics.md:355-379] — Story 2.2 user story + all 4 BDD ACs verbatim (background tick → READ pool → 2.1 projection → atomic swap; mutation-path refresh; stale-not-blank + per-repo degrade; ≤3s fan-out perf with documented cliff).
- [Source: epics.md:327-329] — Epic 2 intent: derive-on-read, "a poll never shells out to git", forward-only story order (projection → cache/refresher → endpoint → UI).
- [Source: epics.md:381-401, 404-474] — 2.3 (`/state`) and 2.4a/b/c (UI) scope fence — what 2.2 must NOT build.
- [Source: architecture.md:350-360] — derive-on-read into an ephemeral in-memory cache, refreshed on background tick + every mutating call; `/state` never shells out; `generated_at`; stale-on-unavailable keeps last-known.
- [Source: architecture.md:362-391] — pinned `CacheSnapshot`/`TaskView`/`WorktreeView` shape (2.2 must NOT change it); join rule; "immutable and swapped whole".
- [Source: architecture.md:393-406] — mutation critical-section ordering (rebuild→swap before return); "last-writer-wins on the ref is correct because each rebuild is a full snapshot" (the correctness basis for Decision A).
- [Source: architecture.md:451-460] — two latency-class pools; READ class = 3s timeout, sem=2, 2s acquire (fail-fast / keep cache); the per-repo degrade mechanism.
- [Source: architecture.md:344-348, 439-443] — orphaned links/untracked worktrees surfaced, NEVER auto-cleaned; crash-safety + reconciliation are v1 non-goals (the refresher reports, never cleans).
- [Source: architecture.md:741-742, 757, 765-781] — `cache.py` location + boundary: "the only writer of the in-memory view"; planned `test_cache.py` coverage.
- [Source: architecture.md:63-76 (Invariants 1,3,4,7,10,11)] — single run_git/READ pool/no destructive read-path op; snake_case; derive-on-read; SDK seam; `now_iso()`.
- [Source: src/dev_helper_mcp/core/worktrees.py:45-112] — the per-repo fan-out + degrade template to mirror (catches `DevHelperError` AND `returncode != 0`, never raises, never deletes).
- [Source: src/dev_helper_mcp/git/runner.py:62-99, config.py:30-44] — `GitRunner.run_git(..., pool=Pool.READ)` semantics + the READ-pool tunables; one runner per app (loop-bound semaphores).
- [Source: src/dev_helper_mcp/git/porcelain.py:44-71] — total `parse_worktree_porcelain`; no `-z`.
- [Source: src/dev_helper_mcp/store.py:286-328] — `list_tasks()` shape = `project()`'s `tasks` arg + the source for the distinct-repo set (no new Store method).
- [Source: src/dev_helper_mcp/tools/handlers.py:30-128, server_factory.py:188-220] — `ToolDeps`/`_DepsHolder`/lifespan wiring the cache + refresher slot into.
- [Source: src/dev_helper_mcp/util.py:6-14] — `now_iso()` for `generated_at`.
- [Source: tests/test_adapter_seam.py:18-19] — `cache.py` already in `SEAM_MODULES` (no edit needed; just stay SDK-free).
- [Source: tests/conftest.py:66-84, 95-105, 142-172] — git-safety guard, state-dir isolation, `tmp_git_repo`.
- [Source: 2-1-derive-on-read-projection-pure-task-grouped.md] — the consumed contract: `project()` signature, `CacheSnapshot`/`TaskView`/`WorktreeView` fields, Decision B (`generated_at` injected by THIS caller), `done`-tasks-surface.
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — `Mount("/")` route-shadowing (a 2.3 concern); no-`-z`; orphan/move reconciliation deferred (v1 non-goal).
- [Source: project-context.md] — SDK seam (cache in scan), async/git discipline (READ pool, no destructive read-path op), snake_case/derive-on-read, `now_iso()`, git-safety-in-tests, testing rules, "this file wins over architecture pseudo-code".

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-25 | Story 2.2 drafted (ready-for-dev): ephemeral in-memory `cache.py` (`Cache` + `run_refresher`) over the 2.1 `project()`; per-repo READ-pool fan-out with independent degrade; post-mutation refresh hook in the 3 mutating handlers; lifespan wiring (shared `GitRunner`, warm-start, launch/cancel refresher). Hard prerequisite: Story 2.1 must be implemented first. |
| 2026-06-25 | Decisions A (adapter-level refresh — core untouched), B (degrade via `repo_unavailable:<repo>` warnings + carry-forward last-known + don't-swap-on-total-fail, no shape change), C (`CACHE_REFRESH_INTERVAL=2.0`) **operator-confirmed**. No open questions remain. |
