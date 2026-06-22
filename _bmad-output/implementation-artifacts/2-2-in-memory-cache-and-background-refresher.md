# Story 2.2: In-memory cache and background refresher

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want an ephemeral in-memory cache rebuilt from a per-repo git fan-out on a background tick and on every mutating tool call,
so that the dashboard reads current state cheaply and a slow or unavailable repo never blanks the board.

## Acceptance Criteria

1. **Background tick → fan-out → projection → atomic swap (FR-9, FR-12).**
   **Given** active tasks spanning several repos,
   **When** the background refresher ticks,
   **Then** it reads each distinct repo's porcelain via the **read pool**, runs the 2.1 projection, and atomically swaps the cache ref to the new immutable snapshot (last-writer-wins on the ref).

2. **Refresh inside the mutation critical section (never `ok` on stale).**
   **Given** a mutating tool call (`create_task` / `remove_worktree` / `update_task`),
   **When** it completes,
   **Then** the cache is refreshed inside the mutation's critical section before the tool returns, so a tool never returns `ok` on stale state.

3. **Stale-not-blank + per-repo degrade (NFR-Reliability, degrade rule).**
   **Given** git is unavailable or a repo's read times out,
   **When** a refresh runs,
   **Then** the cache keeps last-known state and marks it stale rather than going blank;
   **And** a single slow/timed-out repo degrades **that repo only** (its worktrees rendered unavailable/last-known) while the rest of the snapshot builds normally.

4. **Fan-out perf/chaos within the ≤3s SLO, bounded to ≤15 repos (NFR-Performance).**
   **Given** ≤ 15 tracked repos,
   **When** the fan-out perf/chaos test runs (parametrized `num_tasks` × `repos_per_task`, a slow-repo injector via `asyncio.sleep`, concurrent readers exercising the 2s pool-acquire),
   **Then** p95 derive latency stays within the ≤3s soft SLO;
   **And** the test documents the latency cliff beyond 15 repos (SLO is explicitly bounded).

## Tasks / Subtasks

- [ ] **Task 1 — `cache.py`: the in-memory cache holder (AC: 1, 2)**
  - [ ] A cache object holding `current: CacheSnapshot` by reference; `get()` returns the ref with no lock (GIL-atomic single-ref read); never returns a torn snapshot
  - [ ] An atomic `swap(new_snapshot)` (single ref assignment); the snapshot is immutable (built by 2.1's `derive()`), so last-writer-wins is correct
- [ ] **Task 2 — `cache.py`: `refresh()` — the fan-out + projection (AC: 1, 3)**
  - [ ] Determine the distinct `repo_path` set from `task_worktree` rows (`store.py`); fan out `git -C <repo> worktree list --porcelain -z` across them via the **read pool** (sem=2, 3s timeout, 2s acquire — from 1.2 `run_git`)
  - [ ] Per-repo degrade: a slow/timed-out/unavailable repo contributes a last-known / "unavailable" listing for **that repo only**; the rest build normally — never raise out of the whole refresh
  - [ ] Call `projection.derive(git_listings, task_rows, link_rows, now_iso())` and `swap()` the result
  - [ ] On a fully-failed refresh (e.g. all git unavailable), keep the last snapshot and mark it **stale** (a stale flag / older `generated_at`) — never swap in a blank snapshot
- [ ] **Task 3 — background refresher tick (AC: 1)**
  - [ ] An asyncio background task started in the app lifespan that calls `refresh()` on an interval (from `config.py`); cleanly cancelled on shutdown
  - [ ] Wire startup/cancel into the app-owned lifespan in `server_factory.py` (adapter layer) — the lifespan already wraps `mcp_app.lifespan`; add the refresher task alongside
- [ ] **Task 4 — wire refresh into the existing mutation tools (AC: 2) — cross-epic integration**
  - [ ] In the mutation handlers from Epic 1 (`create_task`, `remove_worktree`, `update_task`), call `cache.refresh()` **inside the mutation's critical section, before the tool returns** (the architecture's step 4→5→7 ordering). A mutation must never return `ok` on stale cache
  - [ ] Keep the refresh inside the per-repo mutex / mutation flow so the just-committed rows are reflected
- [ ] **Task 5 — `config.py`: refresher interval (AC: 1)**
  - [ ] Add the background tick interval (and reuse existing read-pool timeouts); no magic numbers in modules
- [ ] **Task 6 — tests (under AR-12 gate)**
  - [ ] `test_cache.py`: refresh tick rebuilds + atomically swaps; stale `generated_at`/flag on git-unavailable (never blank); per-repo degrade (one slow repo → only its lines "unavailable", board still renders); mutation refreshes cache before returning
  - [ ] `test_perf_fanout.py`: parametrized `(num_tasks, repos_per_task)`; slow-repo injector via `asyncio.sleep`; assert p95 derive ≤3s for ≤15 repos; exercise the 2s acquire cliff under concurrent readers; document/log the cliff beyond 15 repos (no silent cap)

## Dev Notes

### Scope boundaries — read first
Builds the **cache + refresher** (the I/O layer that feeds 2.1's pure projection). **OUT of scope:** the `/state` endpoint (Story 2.3 — this story exposes the cache object, not the HTTP route); any HTML/CSS/JS (2.4a–c). The cache is **ephemeral** — rebuilt from scratch on restart; there is NO persisted derived state (that would re-introduce drift). [Source: architecture.md#Derived State & Refresh Model]

### Mutation critical-section ordering (pinned — architecture.md § Derived State & Refresh Model)
```
1 acquire mutation-pool slot           5 atomically swap cache ref to the new snapshot
2 run_git mutation (mutation pool)     6 release slot
3 on git ok: UPSERT rows (aiosqlite)   7 return envelope (data = just-built snapshot or affected view)
4 rebuild snapshot: read porcelain (read pool) + SELECT rows → project
```
Step 5 (refresh) is **inside** the mutation's critical section, before the return (step 7). Read-only tools and `/state` read `cache.current` by ref — no lock. The background tick performs the same 4→5 rebuild; last-writer-wins on the ref is correct because each rebuild is a full immutable snapshot (no partial merge). [Source: architecture.md#Derived State & Refresh Model]

### Builds on Stories 1.2, 2.1, and Epic 1 tools (previous-story intelligence)
- From **2.1**: call `projection.derive(...)` — the pure join. This story supplies the I/O it deliberately omitted. Do not re-implement the join.
- From **1.2**: use `run_git()` **read pool** for the per-repo `worktree list` fan-out (the read pool's 2s-acquire/3s-timeout is what bounds the SLO and enables per-repo degrade); `store.py` for the distinct-repo set + rows. **Read/refresh ops do NOT take the per-repo mutex** (invariant 12).
- **Cross-epic touch (flag):** AC 2 requires editing Epic 1's mutation handlers to call `cache.refresh()` in-section. In Epic 1 those tools had no cache to refresh; this story wires it. Verify the Epic 1 tool tests still pass (they should — refresh is additive). Keep the refresh failure-tolerant so a refresh hiccup doesn't fail an otherwise-successful mutation (but the architecture's intent is the mutation reflects its own committed rows — prefer reflecting them).
- `cache.py` is adapter-adjacent but **core layer** for the refresh logic; it imports `store`/`git`/`projection` (all core) — NOT `mcp`/`starlette`. The lifespan wiring that *starts* the tick lives in `server_factory.py` (adapter). Keep `test_adapter_seam.py` green.

### Binding invariants
- **Invariant 4** — never persist derived state (cache is in-memory only). **Invariant 5** — `/state` reads cache only (enforced in 2.3; here ensure the cache is the single read source). **Invariant 6** — no blocking call on the loop (fan-out via `run_git`). **Invariant 12** — refresh does NOT take the per-repo mutex; mutations do. [Source: architecture.md#Invariants]

### Per-repo degradation (architecture.md § Frontend Architecture / Per-repo fan-out degradation)
The refresh fans `git worktree list` across every tracked repo via the read pool; a slow/timed-out repo **degrades that repo only** — its worktrees render "unavailable"/last-known while the rest renders normally. One slow (monorepo/networked) repo MUST NOT blank or fail the whole board. This requires per-repo error isolation in the fan-out (gather with per-repo try/except, not one all-or-nothing await). [Source: architecture.md#Per-repo fan-out degradation; epics.md#Story 2.2 AC3]

### Source tree components to touch
`cache.py` (new), `core/projection.py` (consume, from 2.1), `server_factory.py` (start/cancel the refresher in the lifespan), `tools/handlers.py` (refresh in mutation sections), extend `config.py`; `test_cache.py`, `test_perf_fanout.py`. [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping]

### Project Structure Notes
- `cache.py` is the ONLY writer of the in-memory derived view. [Source: architecture.md#Architectural Boundaries]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 2.2: In-memory cache and background refresher] — acceptance criteria + risk notes (per-repo degrade; SLO bounded to ≤15 repos)
- [Source: epics.md#FR-9] auto-refresh ≤3s; [Source: epics.md#FR-12] derive-on-read view; [Source: epics.md#NFR-1] performance/fan-out bound
- [Source: architecture.md#Derived State & Refresh Model] — cache, refresh, mutation critical-section ordering
- [Source: architecture.md#Per-repo fan-out degradation]; [Source: architecture.md#Invariants] — 4,5,6,12

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
