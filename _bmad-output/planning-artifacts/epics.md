---
stepsCompleted: [1, 2, 3, 4]
status: 'complete'
completedAt: '2026-06-22'
inputDocuments:
  - '_bmad-output/planning-artifacts/prds/prd-dev-helper-mcp-2026-06-19/prd.md'
  - '_bmad-output/planning-artifacts/prds/prd-dev-helper-mcp-2026-06-19/addendum.md'
  - '_bmad-output/planning-artifacts/architecture.md'
  - '_bmad-output/planning-artifacts/ux-designs/ux-dev-helper-mcp-2026-06-22/DESIGN.md'
  - '_bmad-output/planning-artifacts/ux-designs/ux-dev-helper-mcp-2026-06-22/EXPERIENCE.md'
amendments:
  - '2026-06-22: multi-repo / global server — see PRD/Architecture Amendment notes'
  - '2026-06-22b: 4-status set {running,blocked,review,done} (blocked=awaiting input, review=awaiting review) + dashboard UX spec (UX-DR1-13); board=3 active columns + folded Done, blocked-emphasis; Story 2.4 split into 2.4a/b/c'
  - '2026-06-22c: implementation-readiness M1 — enforced pre-commit quality gate (AR-12) moved from Story 3.3 into Story 1.1 so it guards Epics 1-2; Story 3.3 reframed to install + full-scope gate confirmation'
---

# dev-helper-mcp - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for dev-helper-mcp, decomposing the requirements from the PRD, Technical Addendum, and Architecture Decision Document into implementable stories.

> **Reflects the 2026-06-22 multi-repo / global-server amendment:** a single global server per
> machine; a **task** spans 1+ repositories (one worktree + `agent/<task>` branch per repo); repos
> supplied implicitly per task; worktrees created task-centrically in one `create_task` call;
> per-task status. Tool surface = **5 task-centric tools**.

## Requirements Inventory

### Functional Requirements

FR-1: An agent creates a task in **one call** (`create_task`) supplying a description, a **set of repository paths** (1+), and a task name; for **each repo** the server creates a worktree at `<repo-parent>/<repo>.worktrees/<task>/` on branch `agent/<task>` from that repo's base ref (default HEAD). Each path is validated as a git repo first; collisions (branch or dir exists in **any** repo) reject; the create is **all-or-nothing across the repo set**; returns `task_id` + per-repo `{repo_path, worktree_path, branch}`.
FR-2: An agent or the dashboard can list all worktrees across **all repos any active task touches**, each with repo, path, branch, and linked task id/status; filterable by repo and/or task; derived from per-repo git (not a cache).
FR-3: An agent or developer can remove **one** tracked worktree (by task+repo or path), optionally deleting its branch; refuses on uncommitted changes without `force`; unmerged-branch deletion requires the distinct `force_unmerged_branch` flag and surfaces the unmerged-commit count first; a task's other-repo worktrees are unaffected.
FR-4: A task is registered (via `create_task`, FR-1) with a description, its set of repositories, and status defaulting to `running`; persisted with stable `task_id` (= `<task>` slug), timestamps, and per-repo `(repo_path, branch, worktree_path)` links; **at most one active (non-`done`) task per `<task>` slug**, enforced atomically; slug reuse allowed after `done`.
FR-5: An agent can update an existing task's status and/or description; updates bump `updated_at`; updating a non-existent task returns a not-found error.
FR-6: Task status is one of a **four-state** set {`running`, `blocked`, `review`, `done`} `[AMENDED 2026-06-22b]`, **one status per task** across all its repos. `blocked` = awaiting user input (stuck mid-work); `review` = agent finished, awaiting the operator's review (active, non-`done`; the tool does not merge); `done` = reviewed/closed (terminal). Any other value rejected; `done` is closed and visually distinguished (dimmed, folded). Transitions among running/blocked/review are legal; `done` is terminal. The dashboard renders the active three as columns + a folded Done count.
FR-7: An agent or the dashboard can list tasks, filterable by status and/or repository, each task including its per-repo worktree links and all model fields.
FR-8: The dashboard displays a board of **tasks**, each showing description, status, and its per-repo worktrees (repo, branch, path), grouped under the task. `[AMENDED 2026-06-22b]` Organized as **three active status columns** (Running | Blocked | Review) with `done` as a foldable `✓ N done` count below the board; blocked is the emphasized alarm state; states distinct by position, color, left bar, and per-card glyph (see UX spec / UX-DR1, UX-DR13).
FR-9: The dashboard updates without manual reload (v1: short-interval polling of `/state`); state changes visible ≤ 3s.
FR-10: The dashboard is read-only — no mutating action (no create/modify/remove of worktrees or tasks, no agent launch).
FR-11: The server advertises a small, discoverable, documented MCP tool surface — **5 task-centric tools** (`create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks`) — with enumerable input/output schemas; well under client caps (~40).
FR-12: Task records + per-repo worktree links persist across restarts (machine-global store) and the view never contradicts git — satisfied by **derive-on-read fanning out per repo** (`git -C <repo> worktree list --porcelain` is the sole truth for worktree existence; SQLite holds only task records + links; the view is a read-time projection grouped by task). Out-of-band created/removed worktrees in any repo are reflected; orphaned links are shown/flagged, never auto-deleted.
FR-13: The developer starts **one global server** (no `--repo`) as a long-lived process bound to `127.0.0.1`, serving both the MCP endpoint and the dashboard for all repos; prints the dashboard URL on startup; **one instance per machine** (global lockfile + port-bind mutex); outlives individual agent sessions; learns repos from `create_task`.

### NonFunctional Requirements

NFR-1 (Performance): Task/state tool calls sub-second; worktree create bounded by git but never approaching the ~5-min transport timeout; dashboard state changes ≤ 3s (soft SLO) **bounded to ≤ 15 tracked repos** `[decided 2026-06-22]` — the live view fans `git worktree list` across every tracked repo under the read pool; a slow repo **degrades that repo only** (rendered unavailable/last-known), never the whole board. Multi-repo create fans `git worktree add` across repos (mutation pool, bounded concurrency).
NFR-2 (MCP-Compatibility / Non-blocking loop): Protocol-synchronous, short-lived tools; NO git shell-out or blocking call on the asyncio event loop — git off-loop via single `run_git()` (`-C <repo>`); DB via `aiosqlite`. Tool count stays small (5).
NFR-3 (Reliability): Records survive restart; view never contradicts git (per-repo derive-on-read); failed git ops leave every repo unchanged; `create_task` is all-or-nothing across repos; all errors structured/typed.
NFR-4 (Security/Locality): Bind `127.0.0.1` only; validate `Origin` on `/mcp` AND dashboard routes; no auth, no secrets.
NFR-5 (Portability): Linux-first; avoid hard platform assumptions (macOS works); lock identity guard degrades on non-Linux.
NFR-6 (Simplicity/Footprint): Single easy global install, minimal deps (`mcp` + `aiosqlite`; Starlette/uvicorn transitive).
NFR-7 (Observability): Local logs (stdlib logging to stderr, level via `DEV_HELPER_LOG`) sufficient to diagnose a failed tool call or an orphaned link.

### Additional Requirements

*(Technical requirements from the Architecture Decision Document, as amended 2026-06-22.)*

- **AR-1 — Starter scaffold (FIRST STORY):** `uv init --package dev-helper-mcp`, `src/` layout, `uv_build`, `pyproject.toml`, `uv.lock`, `.python-version` (3.12), `.gitignore`. Pin `mcp>=1.28,<2`, `aiosqlite`.
- **AR-2 — SDK adapter / layer boundary:** core logic (`core/`, `git/`, `store`, `projection`, `cache`, `errors`, `util`) imports no `mcp`/`starlette`; only the adapter layer touches the SDK (v2-migration seam).
- **AR-3 — MCP mount wiring:** `streamable_http_path="/"` + `Mount("/mcp", …)` (no 307); app-owned lifespan wraps `async with mcp_app.lifespan(mcp_app)`.
- **AR-4 — Origin-validation middleware:** our own, outermost over `/mcp` AND dashboard routes; non-allowlisted Origin → 403; absent → allow.
- **AR-5 — `run_git()` single chokepoint + two pools, multi-repo:** every git call through one `run_git()` (`create_subprocess_exec`, pinned env, **`-C <repo>`**, `-z` porcelain, timeout→kill+reap). Read pool (3s, sem=2) vs mutation pool (~120s, sem=4). The read-pool semaphore bounds the per-repo fan-out on refresh.
- **AR-6 — Persistence (`Store`, two tables, machine-global):** `aiosqlite`, WAL + `busy_timeout`, `PRAGMA foreign_keys=ON`; `task(task_id PK=<task> slug, status CHECK IN ('running','blocked','review','done')` `[AMENDED 2026-06-22b]`, …)` + `task_worktree(task_id, repo_path, branch, worktree_path, PK(task_id,repo_path)) ON DELETE CASCADE`; UPSERT for slug reuse; version-check migrations. DB at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`.
- **AR-7 — Derive-on-read cache, multi-repo, grouped by task:** pure projection `(per-repo git_listings, task records) → view` into an ephemeral in-memory cache; refreshed on background tick + every mutating tool call; `/state` reads cache only; stale-on-git-unavailable, never blank. `CacheSnapshot` is grouped by task (TaskView → per-repo WorktreeView).
- **AR-8 — Error taxonomy (typed, stable codes):** `BranchExists`, `WorktreePathInUse`, `BaseRefNotFound`, `DirtyWorktree`, `UnmergedBranch`, `TaskNotFound`, `ActiveTaskConflict`, `LockedWorktree`, `InvalidTaskName`, `GitTimeout`, `InstanceConflict`, **`NotAGitRepo`**, **`RollbackIncomplete`**, `PortUnavailable`, `Internal`; `{ok,data,error}` envelope on every tool.
- **AR-9 — Slug rules:** lowercase, hyphenate, collapse dupes, max 60; reject empty/reserved/`.`/`..`; slug = `task_id` = `agent/<task>` branch in **every** repo; collision in any repo → reject (all-or-nothing); pinned regex before shell-out; `--` end-of-options.
- **AR-10 — Single-instance (machine-global) + port fallback:** global `${XDG_STATE_HOME}/dev-helper-mcp/server.lock` `{pid, port, start_ts}`; atomic `O_CREAT|O_EXCL`; stale-reclaim (pid + Linux identity guard, degraded elsewhere); port auto-fallback 8765→8775 (or strict `--port`); port-bind authoritative mutex; `stop`/`--release-lock`. **No `--repo`.**
- **AR-11 — Conventions:** `snake_case` everywhere; `now_iso()` (UTC ISO-8601 `Z`); Pydantic `*In` models (`CreateTaskIn{task_name, description, repos[], base_ref?}`, `UpdateTaskIn`, …) at the boundary (core takes plain args).
- **AR-12 — Quality gate (no CI v1):** enforced pre-commit hook (`ruff check`, `ruff format --check`, `pytest`) **bootstrapped in Story 1.1 `[AMENDED 2026-06-22c]`** so it guards every subsequent story; `tests/` mirrors `src/`; in-process `httpx.ASGITransport` harness + one real-port uvicorn smoke test asserting `127.0.0.1` bind. `[AMENDED 2026-06-22b]` Dashboard UX-DRs are tested **browser-free** (no Playwright/Cypress): HTML-output assertions (an HTML parser, e.g. `selectolax`), static CSS/JS lint (grep for motion/external-asset tokens), a pure WCAG contrast-ratio check over enumerated token pairs, and a `node --test` unit test for the poller `diff()` — the one small JS test added to the gate.
- **AR-13 — `create_task` atomicity (error-safe) `[AMENDED; decided 2026-06-22]`:** all-or-nothing across N repos. **Preflight** all repos (git-repo? `agent/<task>` branch/dir free? base ref?) before mutating any. Provision worktrees, then commit `task` + `task_worktree` rows **last** in one SQLite transaction on full success. On a later-repo failure, **compensate** (`worktree remove --force` + `branch -D agent/<task>`) and persist no rows; if a compensation itself fails, return **`RollbackIncomplete`** naming the orphaned repos (never silent). **Crash-safety = documented v1 non-goal** (residue = no-DB-row orphan worktree, surfaced by derive-on-read, recoverable on retry; no startup reconciliation engine). Repo set fixed at create (incremental `add_worktree` deferred).
- **AR-14 — Per-repo mutation mutex `[decided 2026-06-22]`:** the global lockfile guards only the process singleton; an in-process **async mutex keyed by `repo_path`** serializes concurrent `create_task`/`remove_worktree` mutations touching the **same** repo (read/refresh ops do not take it). Without it, concurrent same-repo `git worktree add` races (data-loss path).

### UX Design Requirements

`[AMENDED 2026-06-22b; revised post-Party-Mode]` A dashboard **UX specification** is the canonical
visual + behavioral contract at `_bmad-output/planning-artifacts/ux-designs/ux-dev-helper-mcp-2026-06-22/`
— `DESIGN.md` (tokens: dark "modern console, compact" palette, 4 status colors, type, spacing,
components), `EXPERIENCE.md` (IA, state patterns, interactions, accessibility, SM-2 glance flow,
**the authoritative UX-DR1–13 with machine-checkable predicates**), reference mock
`mockups/key-screen-board.html`. Board = **3 active columns** (Running | Blocked | Review) + a foldable
`✓ N done` count; **blocked** is the emphasized alarm state; **no motion**; **diff-and-patch** poller.
Test strategy is **browser-free** (HTML-output asserts + static CSS/JS lint + WCAG-contrast math +
`node --test` for the diff fn — no Playwright). UX-DRs (each covered by an Epic 2 story):

- **UX-DR1** — 3 active columns + folded Done (not a column), grouped by task, per-repo worktrees nested. → 2.4a *(FR-8; FR-6.)*
- **UX-DR2** — Summary count bar; counts equal rendered columns/disclosure; zero-counts shown. → 2.4a
- **UX-DR3** — Per-card non-color encoding: column + left bar + per-card glyph (●/▲/◆/✓) + `data-status`; badges "needs input"/"awaiting review" (never "merge"). → 2.4a *(Accessibility.)*
- **UX-DR4** — Static, no motion; **blocked** is the only lifted card, running flat, done dimmed. (grep CSS/JS for forbidden tokens.) → 2.4a
- **UX-DR5** — Stable render via diff-and-patch: key by `task_id`, content-hash; identical snapshot → 0 DOM writes; status change → reparent. (`diff(x,x)===[]` under `node --test`; MutationObserver 0 mutations.) → 2.4b *(FR-9.)*
- **UX-DR6** — Subordinate freshness; stale treatment when age **> 2× poll-interval**. → 2.4c *(FR-9.)*
- **UX-DR7** — Demoted Done + orphan `<details>` below board, collapsed by default, never auto-expanded. → 2.4c *(FR-12 view.)*
- **UX-DR8** — git-unavailable labeled last-known (never blank); per-repo degrade (slow repo → only its lines "unavailable"). → 2.4c + Story 2.2
- **UX-DR9** — Empty states: empty column header+"0"; empty Blocked = "Nothing needs you"; empty board informative line; zero done → no disclosure. → 2.4c
- **UX-DR10** — Self-contained: inline CSS/JS, system fonts, no external assets/egress. (grep rendered HTML.) → 2.4a *(NFR-Security/Locality, NFR-Simplicity.)*
- **UX-DR11** — WCAG AA contrast over enumerated token pairs (text ≥4.5:1, non-text ≥3:1); pure contrast-math test. → 2.4a
- **UX-DR12** — Overflow contract: active columns fit / scroll within column; board never scrolls horizontally (3→1 wrap). → 2.4a
- **UX-DR13** — Done is a folded count, not a column; done count stays in the summary bar when folded. → 2.4a/2.4c

### FR Coverage Map

FR-1: Epic 1 — create_task: per-repo worktree+branch creation (all-or-nothing)
FR-2: Epic 1 — list_worktrees across repos
FR-3: Epic 1 — remove_worktree (two-guard, per-worktree)
FR-4: Epic 1 — register task via create_task; one-active-per-slug; persistence (Store)
FR-5: Epic 1 — update_task status/description
FR-6: Epic 1 — canonical per-task status set
FR-7: Epic 1 — list_tasks (filter by status/repo)
FR-8: Epic 2 — task-grouped monitoring view
FR-9: Epic 2 — auto-refresh ≤3s (polling)
FR-10: Epic 2 — read-only guarantee
FR-11: Epic 1 — discoverable 5-tool surface
FR-12: Epic 2 (primary, derive-on-read view) — persistence half (Store) established in Epic 1
FR-13: Epic 3 (primary, full global lifecycle/single-instance) — minimal runnable global server bootstrapped in Epic 1

**All FR-1–13 covered.** Two FRs split with a primary home noted: FR-12 (machine-global Store in Epic 1, per-repo derive-on-read view in Epic 2) and FR-13 (minimal global server bootstrap in Epic 1, full single-instance/lockfile/port/install in Epic 3).

`[AMENDED 2026-06-22b]` **All UX-DR1–13 covered** by Epic 2: UX-DR1/2/3/4/10/11/12/13 → **Story 2.4a** (board structure, summary bar, per-card non-color encoding, no-motion+blocked-emphasis, self-contained, contrast, overflow, Done-fold); UX-DR5 → **Story 2.4b** (diff-and-patch stable render); UX-DR6/7/8/9 → **Story 2.4c** (freshness/stale, demoted Done+orphan disclosures, git-unavailable + per-repo degrade [w/ Story 2.2], empty states). The 4-status set (FR-6) is written/validated in Story 1.6 (incl. the `done`-terminal transition matrix), the active=non-done invariant pinned in Story 1.3, and rendered as 3 columns + folded Done in Story 2.4a.

## Epic List

### Epic 1: Agent multi-repo task self-service over MCP
Start the single global `dev-helper-mcp` server, register it once in Claude Code, and let an agent fully self-service tasks through MCP: create a task spanning 1+ repos (one worktree + `agent/<task>` branch per repo, all-or-nothing), update its status across the four-state set (`running` · `blocked`=awaiting input · `review`=awaiting review · `done`=terminal), and list/remove worktrees and tasks. Carries the walking-skeleton foundation (uv scaffold, SDK adapter seam, `/mcp` mount + lifespan, Origin middleware + 127.0.0.1 bind, single `run_git()` + two pools with `-C <repo>`, two-table machine-global `Store`, slug rules, error taxonomy incl. `NotAGitRepo`/`RollbackIncomplete`, conventions, a minimal runnable global server). Delivers the persistence half of FR-12.
**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-5, FR-6, FR-7, FR-11
**Story-decomposition guidance (foundation-first, from Party Mode 2026-06-22 — finalize in Step 3):** (1.1) walking skeleton — scaffold + `/mcp` mount/lifespan + Origin middleware + one no-op tool round-trips + **enforced pre-commit quality gate (AR-12, moved here `[AMENDED 2026-06-22c]`)**, no git/DB; (1.2) git + Store substrate — `run_git()`+pools, porcelain parse, two-table Store, slug, lockfile; (1.3) `create_task` happy path (single- then multi-repo, all succeed); (1.4) **`create_task` rollback (AR-13) as its own story** — error-safe compensation + `RollbackIncomplete`, behind a testable `RepoMutator` seam for fault injection; (1.5) `list_worktrees`/`remove_worktree`; (1.6) `update_task`/`list_tasks` status lifecycle. Risk ACs: AR-14 per-repo mutex serializes same-repo mutations (concurrency test); preflight-before-mutate; rollback-failure matrix (fail on repo i of N, incl. failure during compensation).

### Epic 2: Live multi-repo monitoring dashboard
The developer opens a browser tab and sees a live, auto-refreshing, **read-only** board of tasks — **three active columns** (Running | Blocked | Review) with `done` folded to a `✓ N done` count, **blocked** emphasized as the alarm, each task grouped with its per-repo worktrees (≤3s freshness for ≤15 repos), orphaned links demoted/labeled — that never contradicts git. Builds the per-repo derive-on-read projection + in-memory cache + background refresher + `/state` + a diff-and-patch UI, to the dashboard **UX spec** (`ux-designs/ux-dev-helper-mcp-2026-06-22/`, UX-DR1–13). Uses Epic 1's tasks/worktrees for content. **Stories:** 2.1 projection · 2.2 cache/refresher · 2.3 `/state` · 2.4a board structure · 2.4b live poller · 2.4c degrade/edge states.
**FRs covered:** FR-8, FR-9, FR-10, FR-12 (derive-on-read view)
**Risk notes (Party Mode 2026-06-22):** per-repo fan-out must **degrade a slow/timed-out repo individually** (render "unavailable"/last-known) — never blank or fail the whole board (AC). SLO ≤3s is **bounded to ≤15 tracked repos**; needs a fan-out performance/chaos test (param `num_tasks`×`repos_per_task`, slow-repo injector via `asyncio.sleep`, exercise the 2s pool-acquire cliff under concurrent readers).

### Epic 3: Reliable global install & lifecycle
The developer installs the tool once (`uv tool install`), runs **one global instance per machine** with automatic port fallback (8765→8775), clean single-instance protection (global lockfile + port-bind mutex), a `stop`/`--release-lock` command, and the dashboard URL printed on startup — no opaque port-in-use errors or stale locks. Includes daily-use packaging; `[AMENDED 2026-06-22c]` the enforced pre-commit quality gate is **established in Story 1.1** (so it guards Epics 1–2) and merely confirmed at full-suite scope here.
**FRs covered:** FR-13
**Risk notes (Party Mode 2026-06-22):** because the lock is now **machine-global**, a stale lock (server `kill -9`'d) blocks *every* repo's agents — so **PID-liveness stale-lock detection** (reclaim a dead PID, refuse a live one) graduates from nice-to-have to a required AC, with its own deterministic test. (Note: this lockfile is the *process singleton* guard; per-repo mutation safety is AR-14, built in Epic 1.)

## Epic 1: Agent multi-repo task self-service over MCP

Start the single global `dev-helper-mcp` server, register it once in Claude Code, and let an agent fully self-service tasks through MCP — create a task spanning 1+ repos (one worktree + `agent/<task>` branch per repo, all-or-nothing), update its status, and list/remove worktrees and tasks. Stories are sequenced **foundation-first** so the highest-risk wiring (transport, security, async git, atomic create) is proven before features build on it. Each story depends only on earlier stories.

### Story 1.1: Runnable, secure global MCP server skeleton

As the developer-operator,
I want to start a single global `dev-helper-mcp` server that an MCP client can connect to over Streamable HTTP on localhost,
So that the transport, mount, and Origin-security foundation is proven end-to-end before any real tools exist.

**Acceptance Criteria:**

**Given** a clean checkout scaffolded with `uv init --package` (`src/` layout, `pyproject.toml`, pins `mcp>=1.28,<2` + `aiosqlite`, `.python-version` 3.12, `.gitignore`),
**When** I run `uv run dev-helper-mcp`,
**Then** the server binds `127.0.0.1` on the first free port in 8765→8775 and prints the dashboard URL on startup.

**Given** the running server,
**When** an MCP client connects to `http://127.0.0.1:<port>/mcp` and lists tools,
**Then** the MCP handshake completes with no 307 redirect (`streamable_http_path="/"` + `Mount("/mcp")`, lifespan wrapped) and a trivial registered health/ping tool round-trips.

**Given** a request carrying a non-allowlisted `Origin` header,
**When** it hits `/mcp` or any route,
**Then** the outermost Origin-validation middleware returns `403`;
**And** a request with an absent `Origin` (non-browser MCP client) is allowed.

**Given** the bound server,
**When** the uvicorn smoke test inspects the bind address,
**Then** it is `127.0.0.1`, never `0.0.0.0`;
**And** no module under `core/`, `git/`, `store`, `projection`, `cache` imports `mcp`/`starlette` (adapter-seam test).

**Given** the scaffold with dev dependencies `ruff` + `pytest` and `tests/` mirroring `src/` (AR-12), `[AMENDED 2026-06-22c]`
**When** the enforced pre-commit hook is installed as part of this first story,
**Then** committing runs `ruff check`, `ruff format --check`, and `pytest` and **blocks the commit on any failure** — establishing the regression gate (no CI in v1) so it guards **every subsequent story** from the start, not just the final one;
**And** the no-op tool round-trip plus the bind and adapter-seam tests above run green under this gate. *(Quality-gate hook is bootstrapped here, NOT deferred to Epic 3 — only `uv tool install` packaging remains in Story 3.3.)*

### Story 1.2: Async git execution and persistence substrate

As the developer building the tool,
I want the single `run_git()` helper (two pools, `-C <repo>`), the two-table machine-global `Store`, slug validation, the typed error taxonomy, and the per-repo mutation mutex,
So that every later tool shares one safe off-loop git path and one atomic persistence layer.

**Acceptance Criteria:**

**Given** a valid git repo path,
**When** `run_git()` runs a read command,
**Then** it executes via `create_subprocess_exec` (never shell) with pinned env (`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `-C <repo>`) under the read pool (3s timeout, sem=2, 2s acquire);
**And** a mutation command runs under the mutation pool (~120s, sem=4).

**Given** a git command that exceeds its timeout,
**When** `run_git()` handles it,
**Then** it kills and reaps the subprocess (no zombie), drains both pipes, and raises `GitTimeout`;
**And** a non-git path raises `NotAGitRepo`.

**Given** a caller-supplied task name,
**When** it is slugified,
**Then** the result is lowercased/hyphenated, collapses duplicate/leading/trailing hyphens, max length 60, and rejects empty/reserved/`.`/`..` with `InvalidTaskName`.

**Given** a fresh state dir,
**When** the Store bootstraps,
**Then** it creates `task` + `task_worktree` (PK `task_id`; PK `(task_id, repo_path)`; `ON DELETE CASCADE`; `PRAGMA foreign_keys=ON`, WAL, `busy_timeout`) at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`;
**And** opening a DB with a newer `PRAGMA user_version` is refused with a clear error.

**Given** two concurrent mutations targeting the same `repo_path`,
**When** they run,
**Then** the per-`repo_path` async mutex serializes them (read/refresh ops do not take it).

### Story 1.3: Create a multi-repo task (happy path)

As a Claude Code agent,
I want to create a task spanning one or more repositories in a single `create_task` call,
So that each repo gets its own isolated worktree + `agent/<task>` branch for my unit of work.

**Acceptance Criteria:**

**Given** one valid repo path `A` and a task name,
**When** I call `create_task(task_name, description, repos=[A])`,
**Then** a worktree is created at `<A-parent>/<A>.worktrees/<task>/` on branch `agent/<task>` from A's HEAD, a `task` row (status `running`) and one `task_worktree` row are persisted, and it returns `{ok:true, data:{task_id, status, worktrees:[{repo_path, worktree_path, branch}]}}` (snake_case, `now_iso()` timestamps).

**Given** several valid repos `[A,B,C]` that all succeed,
**When** I call `create_task`,
**Then** one worktree + `agent/<task>` branch is created per repo and one `task_worktree` row per repo is committed in a single SQLite transaction (rows written last, after all worktrees succeed).

**Given** an optional `base_ref`,
**When** provided,
**Then** each repo's worktree is created from that ref (which must exist in every requested repo).

**Given** an active task already using the same `<task>` slug,
**When** `create_task` is called,
**Then** `ActiveTaskConflict` is returned and nothing is created — where **"active" is defined as `status != 'done'`**, so `running`, `blocked`, **and `review`** all conflict (regression test: `create → update_task(review) → create same slug` rejects; `create → update_task(done) → create same slug` succeeds). The predicate must NOT be an enumerated allowlist that silently omits `review`. *(Party Mode 2026-06-22: highest-risk seam of the 4-status change.)*

**Given** the `agent/<task>` branch or target directory already exists in any requested repo (preflight),
**When** `create_task` is called,
**Then** `BranchExists` / `WorktreePathInUse` is returned before any repo is mutated.

### Story 1.4: `create_task` cross-repo rollback (error-safe)

As a Claude Code agent,
I want a partially-failed `create_task` to leave every repo and the store exactly as before the call,
So that a failure never leaves orphaned worktrees, branches, or task records.

**Acceptance Criteria:**

**Given** repos `[A,B,C]` where worktree creation in C fails (`NotAGitRepo` / `BranchExists` / `BaseRefNotFound`),
**When** `create_task` runs,
**Then** the worktrees already created in A and B are removed (`git worktree remove --force`) AND their `agent/<task>` branches deleted (`git branch -D`), in reverse order;
**And** no `task` / `task_worktree` rows persist (the DB looks like the call never happened).

**Given** a preflight failure (a repo invalid before any mutation),
**When** `create_task` runs,
**Then** nothing is created in any repo (the cheapest rollback — don't start).

**Given** a compensating teardown that itself fails,
**When** rollback runs,
**Then** `RollbackIncomplete` is returned with `details` listing the repo paths left orphaned, preserving the original cause as the failure reason; nothing is swallowed.

**Given** a clean rollback,
**When** `create_task` is retried with the same `task_name`,
**Then** it succeeds (no residual slug / branch / directory collision).

**Given** the worktree-mutation logic sits behind a `RepoMutator` seam,
**When** tests inject `FlakyMutator(fail_on_repo=i, fail_on_phase={add|remove})`,
**Then** the partial-failure matrix (fail on repo i of N for i∈{1,2,N}, N∈{1,2,3}) is deterministic and asserts zero worktrees, zero branches, zero rows.

*Note:* crash-safety (SIGKILL mid-create) is an explicit v1 non-goal — residue is a no-DB-row orphan worktree surfaced later by derive-on-read (Epic 2), recoverable on retry; no startup reconciliation sweep is built.

### Story 1.5: List and remove worktrees

As a Claude Code agent or the developer,
I want to list worktrees across all tracked repos and remove one when its work is done,
So that I can see every isolated checkout and clean it up safely without touching the task's other repos.

**Acceptance Criteria:**

**Given** tasks with worktrees across several repos,
**When** I call `list_worktrees(repo?, task_id?)`,
**Then** it returns each worktree's `repo_path`, `worktree_path`, `branch`, and linked `task_id`/`status`, filtered as requested, derived from per-repo `git worktree list --porcelain -z` (not a stale cache).

**Given** a worktree identified by `task_id`+`repo` (or by path),
**When** I call `remove_worktree`,
**Then** that repo's working tree is removed and de-tracked and its `task_worktree` row dropped;
**And** the task's worktrees in other repos are unaffected.

**Given** a worktree with uncommitted changes,
**When** `remove_worktree` is called without `force`,
**Then** `DirtyWorktree` is returned and nothing changes;
**And** with `force=true` it is removed.

**Given** `delete_branch=true` for a branch with unmerged commits,
**When** `remove_worktree` is called without `force_unmerged_branch`,
**Then** `UnmergedBranch` is returned, surfacing the unmerged-commit count first;
**And** with `force_unmerged_branch=true` the branch is deleted.

**Given** a task whose last remaining worktree is removed,
**When** the removal completes,
**Then** the task record is marked closed/detached.

### Story 1.6: Update and list tasks (status lifecycle) and complete the tool surface

As a Claude Code agent,
I want to update a task's status and description and query tasks by status or repo,
So that I can self-report progress and a monitoring view can reflect it.

**Acceptance Criteria:**

**Given** an existing task,
**When** I call `update_task(task_id, status?, description?)`,
**Then** the status and/or description are updated and `updated_at` is bumped;
**And** a status outside the four-state set {`running`,`blocked`,`review`,`done`} is rejected (`blocked`=awaiting input, `review`=awaiting review).

**Given** the status transition graph,
**When** `update_task` changes status,
**Then** transitions among `running` ↔ `blocked` ↔ `review` are all legal, and **`done` is terminal** — a task in `done` cannot be moved back to an active status (re-activating a slug is done via a new `create_task`, not `update_task`); the full 4×4 transition matrix is enforced (legal set passes, illegal `done → *` rejects), asserted by a parametrized table test. *(Party Mode 2026-06-22: transition legality was a spec gap; `done` terminal is the policy.)*

**Given** a non-existent `task_id`,
**When** `update_task` is called,
**Then** `TaskNotFound` is returned.

**Given** a task updated to `done`,
**When** it completes,
**Then** it no longer counts as the active task for its `<task>` slug (the slug becomes reusable) and is flagged closed (moves to the folded Done section on the dashboard).

**Given** existing tasks,
**When** I call `list_tasks(status?, repo?)`,
**Then** the matching tasks are returned with all model fields including their per-repo `(repo_path, branch, worktree_path)` links.

**Given** a connected MCP client,
**When** it enumerates the tool surface,
**Then** exactly **5** tools are advertised with input/output schemas — `create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks` — all returning the `{ok,data,error}` envelope with snake_case keys.

## Epic 2: Live multi-repo monitoring dashboard

The developer opens a browser tab and sees a live, auto-refreshing, **read-only** board of tasks — each grouped with its per-repo worktrees and status — that never contradicts git. Built derive-on-read: a pure projection over per-repo git listings × task records, an ephemeral in-memory cache, and a polled `/state` endpoint, so a poll never shells out to git. Uses Epic 1's tasks/worktrees and `run_git()`/`Store`; nothing in Epic 1 depends on it. Stories are forward-only: pure projection → cache/refresher → endpoint → UI.

### Story 2.1: Derive-on-read projection (pure, task-grouped)

As the developer building the tool,
I want a pure function that joins live per-repo git worktree listings with the Store's task records into a task-grouped view,
So that the dashboard view is always a faithful projection of git with no stored derived state to drift.

**Acceptance Criteria:**

**Given** per-repo `git worktree list --porcelain` outputs and the Store's `task` + `task_worktree` rows,
**When** the projection runs,
**Then** it returns a `CacheSnapshot` grouped by task (`TaskView` → per-repo `WorktreeView`) with `generated_at`, tasks sorted by `task_id` ASC and worktrees by `repo_path` ASC.

**Given** a `task_worktree` link whose `branch` is absent from its repo's porcelain,
**When** the projection runs,
**Then** that worktree is emitted with `orphaned: true` AND surfaced in `warnings` as `orphan_link:<task_id>@<repo>:<branch>`; it is never auto-deleted and never auto-`done`.

**Given** a worktree present in a repo's git with no matching link,
**When** the projection runs,
**Then** it is surfaced as a task-less/untracked entry, not dropped.

**Given** the projection function,
**When** it executes,
**Then** it performs no writes, no git/DB I/O, and no destructive git op (purity test), and is total — it never throws on orphans or malformed-but-parsed input.

### Story 2.2: In-memory cache and background refresher

As the developer-operator,
I want an ephemeral in-memory cache rebuilt from a per-repo git fan-out on a background tick and on every mutating tool call,
So that the dashboard reads current state cheaply and a slow or unavailable repo never blanks the board.

**Acceptance Criteria:**

**Given** active tasks spanning several repos,
**When** the background refresher ticks,
**Then** it reads each distinct repo's porcelain via the **read pool**, runs the 2.1 projection, and atomically swaps the cache ref to the new immutable snapshot (last-writer-wins on the ref).

**Given** a mutating tool call (`create_task` / `remove_worktree` / `update_task`),
**When** it completes,
**Then** the cache is refreshed inside the mutation's critical section before the tool returns, so a tool never returns `ok` on stale state.

**Given** git is unavailable or a repo's read times out,
**When** a refresh runs,
**Then** the cache keeps last-known state and marks it stale rather than going blank;
**And** a single slow/timed-out repo degrades **that repo only** (its worktrees rendered unavailable/last-known) while the rest of the snapshot builds normally.

**Given** ≤ 15 tracked repos,
**When** the fan-out perf/chaos test runs (parametrized `num_tasks` × `repos_per_task`, a slow-repo injector via `asyncio.sleep`, concurrent readers exercising the 2s pool-acquire),
**Then** p95 derive latency stays within the ≤3s soft SLO;
**And** the test documents the latency cliff beyond 15 repos (SLO is explicitly bounded).

### Story 2.3: Read-only `/state` endpoint

As the dashboard client,
I want a `/state` JSON endpoint served from the in-memory cache,
So that I can poll current state cheaply and safely without triggering any git work or mutation.

**Acceptance Criteria:**

**Given** the in-memory cache,
**When** `GET /state` is called,
**Then** it returns the current `CacheSnapshot` as snake_case JSON including `generated_at`, read from the cache by reference only — no git shell-out on the poll path.

**Given** any dashboard route including `/state`,
**When** a request carries a non-allowlisted `Origin`,
**Then** it is rejected `403` by the same outermost middleware as `/mcp`;
**And** an absent `Origin` is allowed.

**Given** the dashboard's served interface,
**When** it is inspected,
**Then** it exposes no mutating route or action (no create/modify/remove of worktrees or tasks, no agent launch) — the read-only guarantee is asserted by test.

*Story 2.4 was split into 2.4a/b/c (Party Mode 2026-06-22): structure, then live poller, then degrade/edge states — each independently testable in a single red-green pass. The UX spec (`DESIGN.md`/`EXPERIENCE.md`) is the binding visual+behavioral contract.*

### Story 2.4a: Static board structure + status encoding

As the developer-operator,
I want the dashboard to render a fixed `/state` payload as a glanceable, accessible board,
So that I can read every task's status by position and shape, not color alone.

**Acceptance Criteria:**

**Given** a fixed `/state` payload,
**When** the page renders,
**Then** the board has **exactly three active columns** — Running | Blocked | Review (lifecycle order) — grouped **by task** (one card per task, per-repo worktrees nested as `repo · branch` lines); `done` is NOT a column but a collapsed `✓ N done` `<details>` below the board; a **summary count bar** shows one pill per status (incl. done) whose counts equal the rendered columns/disclosure, zero-counts shown. **(UX-DR1, UX-DR2, UX-DR13)**

**Given** any task card,
**When** it renders,
**Then** status is encoded by **column + colored left bar + a per-card glyph (●/▲/◆/✓) + a `data-status` attribute** — never color alone; **blocked** is the only lifted card (running flat, done dimmed); reason badges read "needs input" (blocked) / "awaiting review" (review) and the markup contains no "merge" string. **(UX-DR3, UX-DR4-emphasis)**

**Given** the rendered page and its assets,
**When** inspected,
**Then** there is no `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth` in the CSS and no `requestAnimationFrame`/timer-driven style mutation in the JS (no motion, `prefers-reduced-motion` safe); all CSS/JS is inline with a system font stack and no external `src`/`href`/`@import`/`http(s)://` references (self-contained); and each enumerated status text/bar token pair meets WCAG AA against `{bg}`/`{surface}` (text ≥4.5:1, non-text ≥3:1) by a pure contrast-ratio check. **(UX-DR4, UX-DR10, UX-DR11)**

**Given** the board container,
**When** an active column overflows,
**Then** it scrolls within the column and the board never scrolls horizontally (3→1 wrap at narrow width). **(UX-DR12)**

### Story 2.4b: Live poller with diff-and-patch stable render

As the developer-operator,
I want the open board to track `/state` live without flicker or losing my place,
So that I can leave the tab open and trust it to silently stay correct.

**Acceptance Criteria:**

**Given** the page open,
**When** an agent updates a task's status,
**Then** the page reflects it within ≤ 3s (≤ 15 repos) with no manual reload (vanilla-JS poll of `/state` ~1–2s).

**Given** the poller's `diff(prev, next)` function keyed by `task_id` with a per-task content hash,
**When** two identical `/state` snapshots are diffed,
**Then** it returns an empty patch set (`diff(x, x) === []`) — asserted by a `node --test` unit test — **and** a `MutationObserver` over the board container records **zero** mutations across the identical poll.

**Given** a task whose status changes between polls,
**When** the patch applies,
**Then** the existing DOM node is **reparented** to the new column (not destroyed/recreated), only changed fields are patched, and the open/closed state of the Done/orphan disclosures and any scroll position are preserved. **(UX-DR5)**

**Given** the served UI,
**When** the user interacts with it,
**Then** it offers no control to create/modify/remove worktrees or tasks or to launch agents (read-only). **(FR-10)**

### Story 2.4c: Freshness, degraded, and empty states

As the developer-operator,
I want the board to stay honest when data is stale, git is down, or there's nothing to show,
So that I'm never misled by a blank or silently-behind dashboard.

**Acceptance Criteria:**

**Given** the freshness stamp,
**When** the snapshot age exceeds **2 × the poll interval**,
**Then** `generated_at` (rendered small/cornered) shows the stale treatment (grey→amber); under the threshold it does not. **(UX-DR6)**

**Given** done tasks and orphaned annotations,
**When** the page renders,
**Then** the `✓ N done` and orphan sections are each a collapsed-by-default `<details>` below the board, self-explaining, never auto-expanded. **(UX-DR7)**

**Given** a git-unavailable refresh or a single slow/timed-out repo,
**When** the page renders,
**Then** it shows labeled last-known data with an explicit "stale — git unavailable" marker (never a blank board), and a single slow repo degrades only its own worktree lines ("unavailable") while other repos render normally. **(UX-DR8)**

**Given** empty states,
**When** the page renders,
**Then** an empty column shows header + "0"; the **empty Blocked column reads "Nothing needs you"**; a fully empty board shows "No active tasks — create one with `create_task`"; a zero-done state omits the done-disclosure. **(UX-DR9)**

## Epic 3: Reliable global install & lifecycle

The developer installs `dev-helper-mcp` once and runs **one global instance per machine** — automatic port fallback, clean single-instance protection with safe stale-lock recovery, a `stop` command, the dashboard URL printed on startup, and the quality gate (established in Story 1.1) confirmed at full scope — so it is a small fast tool they install once and trust to stay out of the way. This hardens the minimal server bootstrapped in Story 1.1; stories are forward-only: lockfile/single-instance → lifecycle CLI → install & gate-confirmation.

### Story 3.1: Machine-global single-instance protection with stale-lock recovery

As the developer-operator,
I want exactly one `dev-helper-mcp` server per machine, with safe recovery from a dead instance's lock,
So that I never hit an opaque port-in-use crash, nor a server permanently blocked by a stale lock after a hard kill.

**Acceptance Criteria:**

**Given** no server running,
**When** I start `dev-helper-mcp`,
**Then** it atomically creates `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/server.lock` via `os.open(O_CREAT|O_EXCL)` with `{pid, port, start_ts}`, binds the port, and proceeds.

**Given** an existing lockfile whose recorded PID is alive (identity-matched on Linux via `/proc/<pid>` start-time / `boot_id`),
**When** a second start is attempted,
**Then** it refuses with a clear `InstanceConflict` message (or attaches), never an opaque `EADDRINUSE`.

**Given** an existing lockfile whose PID is dead or fails the identity guard,
**When** a new server starts,
**Then** it reclaims the lock via atomic-rename takeover and proceeds.

**Given** the chosen port is already bound by another process,
**When** the server binds,
**Then** the port-bind is the authoritative mutex — `EADDRINUSE` ⇒ `InstanceConflict` regardless of lock state (so PID-reuse false positives are non-fatal).

**Given** a non-Linux platform,
**When** the identity guard cannot run,
**Then** it degrades to PID-liveness only with a startup warning, the port-bind mutex remaining authoritative (NFR-Portability).

### Story 3.2: Server lifecycle CLI — port control, stop, graceful release

As the developer-operator,
I want explicit control over the port and a clean way to stop the server,
So that I can run and release the single global instance without reaching for `kill -9` or `rm -rf` on the lockfile.

**Acceptance Criteria:**

**Given** `--port N`,
**When** I start the server,
**Then** it binds exactly N or fails with `PortUnavailable` (strict override, no fallback); without `--port` it scans 8765→8775 and binds the first free port.

**Given** the server has bound a port,
**When** it starts up,
**Then** the actual bound port is written to the lockfile and printed with the dashboard URL, and the dashboard reads the bound port from the lockfile (never a hardcoded constant); there is **no `--repo` flag** (the server is global).

**Given** a running server,
**When** I run `dev-helper-mcp stop` (or `--release-lock`),
**Then** the running instance is signaled to shut down cleanly and the lockfile is released.

**Given** a clean shutdown via signal or `atexit`,
**When** the process exits,
**Then** the lockfile is released; the unclean path is covered by the stale-lock tolerance of Story 3.1.

### Story 3.3: Daily-use install (quality gate from Story 1.1 confirmed at full scope)

As the developer-operator,
I want to install the tool once as a console command, with the quality gate (established in Story 1.1) confirmed to cover the complete v1 suite,
So that it is a trustworthy daily tool and regressions are caught even without CI.

**Acceptance Criteria:**

**Given** the project (`src/` layout, `uv_build` backend),
**When** I run `uv tool install` (or pipx),
**Then** a `dev-helper-mcp` console entry point is installed and runnable from any directory.

**Given** the enforced pre-commit hook **established in Story 1.1** (it was NOT deferred to this story), `[AMENDED 2026-06-22c]`
**When** the full v1 suite exists (Epics 1–3 stories complete),
**Then** the hook still enforces `ruff check`, `ruff format --check`, and `pytest` (the in-process ASGI suite; the real-port uvicorn smoke test may be slow-marked/opt-in) on every commit — this story **confirms the gate scales to the complete suite**, it does not introduce it.

**Given** the test suite,
**When** a CI runner is later introduced,
**Then** the suite runs unchanged (it is CI-ready).

**Given** the running server,
**When** it logs,
**Then** it writes stdlib `logging` to stderr at a level set by `DEV_HELPER_LOG` (default `INFO`), sufficient to diagnose a failed tool call or an orphaned link, never logging secrets or full annotation contents at `INFO` (NFR-Observability).
