---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-06-22'
inputDocuments:
  - '_bmad-output/planning-artifacts/prds/prd-dev-helper-mcp-2026-06-19/prd.md'
  - '_bmad-output/planning-artifacts/prds/prd-dev-helper-mcp-2026-06-19/addendum.md'
workflowType: 'architecture'
project_name: 'dev-helper-mcp'
user_name: 'Dev'
date: '2026-06-19'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

> ## ⚠️ AMENDMENT 2026-06-22 — multi-repo / global server (GOVERNING)
>
> The PRD was amended (see `prd.md` Amendment note) so v1 is a **single global server per machine**
> and a **task may span multiple repositories** — one worktree + `agent/<task>` branch **per repo**.
> Where any decision below assumes one configured repo, read it through these binding changes
> (each is also applied inline below, tagged `[AMENDED 2026-06-22]`):
>
> 1. **No `--repo`; the server is machine-global.** A repo becomes known when a task names it
>    (the agent passes an **absolute repo path** in `create_task`; validated as a git repo). Every
>    `run_git` is `-C <repo>` for the relevant repo. One global MCP server registered in Claude Code.
> 2. **Schema is two tables, task-keyed (NOT one row per branch):** `task(task_id PK = <task> slug,
>    description, status, created_at, updated_at)` + `task_worktree(task_id, repo_path, branch,
>    worktree_path, PRIMARY KEY(task_id, repo_path))`. The one-active invariant is now **one active
>    task per `<task>` slug** (task PK + status), and **one worktree per repo per task**
>    (task_worktree PK).
> 3. **Derive-on-read fans out across repos.** For each distinct `repo_path` in `task_worktree`, run
>    `git -C <repo> worktree list --porcelain` and left-join annotations by `(repo, branch)`. Existence
>    truth = per-repo git porcelain. The cached view is grouped **by task** (see updated CacheSnapshot).
> 4. **DB + lockfile are machine-global** (XDG state dir, e.g.
>    `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/{state.db,server.lock}`), NOT in-repo. Worktrees
>    still live as siblings of each repo.
> 5. **Single-instance is per-machine** (one global lockfile + port-bind mutex), not per-repo.
> 6. **Tool surface = 5 task-centric tools:** `create_task` (replaces `create_worktree` +
>    `register_task`; creates one worktree per repo, all-or-nothing), `list_worktrees`,
>    `remove_worktree`, `update_task`, `list_tasks`.
> 7. **Status is per task** (one status across all its repos). `[AMENDED 2026-06-22b]` Canonical set
>    is **four** states: `{running, blocked, review, done}` — `blocked`=awaiting user input,
>    `review`=finished/awaiting review-merge (active, non-`done`), `done`=terminal. Schema `CHECK`,
>    `update_task` enum, and the dashboard's 4 status columns all reflect this.
>
> Unchanged and still binding: derive-on-read purity, the single `run_git()` + two pools, the
> `{ok,data,error}` envelope, snake_case, 127.0.0.1 bind + Origin validation, the SDK-isolation seam,
> the two destructive-op guards, `now_iso()`. The CLI loses `--repo`; everything else holds.

## How to Use This Document (read first)

**Reading order for an implementing agent:**
1. **§ Invariants** + **§ Decisions at a Glance** (below) — the binding rules and verdicts. Obey these.
2. **Project Context Analysis** — background (what & why). Context, not contract.
3. **Starter Template Evaluation** + **Core Architectural Decisions** — the **binding contract**.
4. **Implementation Patterns & Consistency Rules** — **binding** conventions (envelope, naming, `run_git`).
5. **Project Structure & Boundaries** — where each thing lives; FR→module map.
6. **Architecture Validation Results** — coverage, required tests, readiness self-check.

### Invariants — Rules That Must Never Be Broken

1. **Every `git` call goes through the single `run_git()` helper** and its correct pool (read vs mutation). Never call `subprocess`/`os.system` for git anywhere else.
2. **Every tool returns the `{ok, data, error}` envelope.** Core logic raises typed `DevHelperError`; the adapter converts. Never return ad-hoc error dicts; never leak a stack trace.
3. **All JSON keys are `snake_case`** (tool I/O and `/state`). No camelCase, no translation layer.
4. **Derive-on-read: never persist derived state.** `[AMENDED 2026-06-22]` Git `worktree list --porcelain` **per tracked repo** is the sole truth for worktree existence; SQLite stores only task records + their per-repo `(repo_path, branch, worktree_path)` links; the view is recomputed (fanning out across the task set's repos) into an ephemeral in-memory cache.
5. **`/state` reads the in-memory cache only — never shells out to git on a poll.**
6. **No blocking call on the asyncio event loop, ever** (git off-loop via `run_git`; DB via `aiosqlite`).
7. **Core logic imports no `mcp` / `starlette`.** Only the adapter layer (`server_factory`, `server`, `middleware`, `tools/`, `dashboard/`, `lock`, `cli`) touches the SDK — this is the v2-migration seam.
8. **MCP mount wiring is REQUIRED and load-bearing:** set `streamable_http_path="/"` + `Mount("/mcp", …)` (no 307); the app-owned lifespan must wrap `async with mcp_app.lifespan(mcp_app)` (or `/mcp` fails "Task group not initialized").
9. **Origin-validation middleware is REQUIRED**, outermost, over `/mcp` AND dashboard routes; bind `127.0.0.1` only (never `0.0.0.0`).
10. **Destructive git ops never run on the read/refresh path**; two distinct removal guards (`force` for dirty worktree, `force_unmerged_branch` for unmerged-branch deletion).
11. **Timestamps** via the single `now_iso()` helper: UTC ISO-8601 with `Z`, second precision.
12. **`[AMENDED 2026-06-22]` Same-repo mutations are serialized by a per-`repo_path` async mutex.** The global lockfile guards only the process singleton; concurrent `create_task`/`remove_worktree` touching the same repo MUST hold that repo's mutex (read/refresh ops do not). `create_task` is **all-or-nothing**: preflight all repos, provision worktrees, commit DB rows last in one transaction; on failure compensate (`worktree remove --force` + `branch -D`); if compensation fails, raise `RollbackIncomplete` (never silent). Full crash-safety is an explicit v1 non-goal.

### Decisions at a Glance

| Decision | Choice | Why (one clause) |
|---|---|---|
| Language / runtime | Python ≥3.10 (target 3.12), uv | user pref, mature SDK, fast tooling |
| MCP framework | official `mcp` SDK v1.28.0, pinned `>=1.28,<2` | reference impl; v2 (2026-07-27) is breaking |
| Transport | Streamable HTTP, long-lived 127.0.0.1 process | only model supporting a persistent shared dashboard |
| Topology `[AMENDED]` | single **machine-global** server (no `--repo`); tasks span repos | one glance across every repo in flight; repos supplied per task |
| HTTP host | app-owned Starlette; `Mount("/mcp")` + dashboard | one middleware chokepoint, no extra web dep |
| Persistence | SQLite via `aiosqlite`, WAL, one `Store` module | event-loop-safe; drift is a model not engine problem |
| Consistency model | **derive-on-read** into ephemeral in-memory cache | makes permanent git↔DB drift structurally impossible |
| Schema `[AMENDED]` | two tables: `task` (PK=`<task>` slug) + `task_worktree` (PK=`task_id,repo_path`) | task spans repos; task PK = one-active-per-slug; link PK = one worktree per repo |
| Migrations | version-check only (`PRAGMA user_version`) | YAGNI for one table |
| Async git | one `run_git()`, 2 pools: read 3s/sem2, mutation ~120s/sem4 | protects the ≤3s read SLA; create can be slow |
| Tool I/O | Pydantic `*In` models; `{ok,data,error}` envelope; snake_case | free schema/validation; agent branches on `error.code` |
| Single-instance `[AMENDED]` | **machine-global** lockfile + port-bind mutex (8765→8775 fallback) | one server per machine; tight error semantics |
| Distribution | console entry point via `uv tool install` | clean daily-use command |
| CI | none for v1; **enforced pre-commit** `ruff`+`pytest` | solo tool; enforcement replaces CI's gate |
| Platform | Linux-first; lock identity guard degrades on non-Linux | matches NFR-Portability |

## Project Context Analysis

### Requirements Overview

**Functional Requirements:** 13 FRs across 5 feature areas.
- *Worktree Management (FR-1–3):* thin, guarded async wrappers over `git worktree`
  add/list/remove. Create makes a worktree at `<repo-parent>/<repo>.worktrees/<task>/`
  on branch `agent/<task>`; collisions reject (no silent suffixing); removal has two
  distinct safety guards (dirty-worktree vs unmerged-branch deletion). Merge-back is
  out of scope, so `agent/<task>` branches accumulate unmerged commits — the real
  data-loss path.
- *Per-Agent Task Tracking (FR-4–7):* small task-annotation model; fixed status set
  {running, blocked, review, done} `[AMENDED 2026-06-22b]` (blocked=awaiting input, review=awaiting the operator's review — tool does not merge; review is non-done/active); sequential reuse after done.
- *Live Web Dashboard (FR-8–10):* read-only, auto-refreshing view of repo × worktrees
  × tasks; no mutating routes.
- *MCP Server & Tool Surface (FR-11):* ~7 discoverable, documented tools; surface stays
  small, well under client tool caps (~40).
- *State Persistence & Lifecycle (FR-12–13):* `[AMENDED 2026-06-22]` task records survive restart;
  one long-lived localhost process **per machine** (global), single-instance, started separately by
  the developer; serves all repos.

**Non-Functional Requirements (architecture drivers):**
- *Non-blocking event loop:* protocol-synchronous tools, but NO git shell-out or blocking
  call on the asyncio event loop — all git runs off-loop (async subprocess / executor).
- *Performance:* task/state calls sub-second; worktree create bounded by git but never
  approaching the ~5-min transport timeout; dashboard reflects changes ≤ 3s (treated as a
  soft SLO measured as a distribution, NOT a hard CI gate — see below).
- *Reliability:* annotations persist across restart; the dashboard never shows a state
  that contradicts git (satisfied at READ time by derive-on-read, not by a stored
  reconciled cache); failed git ops leave the repo unchanged; structured/typed errors.
- *Security/Locality:* bind 127.0.0.1 only; validate `Origin` header on BOTH the MCP
  endpoint and dashboard routes (DNS-rebinding defense); no auth, no secrets.
- *Portability:* Linux-first, no hard platform assumptions (macOS should work).
- *Simplicity/Footprint:* single easy local install, minimal dependencies.
- *Observability:* local logs sufficient to diagnose a failed tool call or an orphaned
  annotation.

**Scale & Complexity:**
- Primary domain: local backend service (MCP server + async git orchestration +
  read-only web dashboard).
- Complexity level: low–medium and now MORE concentrated/contained after the
  derive-on-read simplification — the highest-risk subsystem (a stateful reconciliation
  engine) has been designed OUT.
- Estimated architectural components: ~5 — MCP tool/transport layer, async git operations
  layer, SQLite annotation store, the derive-on-read projection (git × annotations join),
  and the dashboard (HTTP + state endpoint + UI) + process/lifecycle bootstrap.

### Key Architectural Direction (set during Party Mode pressure-test)

> `[AMENDED 2026-06-22]` This subsection predates the multi-repo amendment and is **context, not
> contract**. Read "keyed by branch name" / "one running annotation per BRANCH" as superseded by the
> governing amendment: state is keyed by **task** (`task` + `task_worktree` tables), one active task
> per **`<task>` slug**, derive-on-read fans `git worktree list` out **per repo**. The derive-on-read
> *principle* below is unchanged.

- **Derive-on-read, NOT reconcile-and-store.** Git (`git worktree list --porcelain`) is
  the SOLE source of truth for worktree existence. The DB stores ONLY task annotations
  keyed by **branch name** (`agent/<task>`). The dashboard/list view is recomputed per
  request as a left-join of live git × annotations — there is no persisted reconciled
  state, no background reconciliation tick, no `link.health` field, no persisted "orphan"
  state. This makes permanent git↔DB drift (R3) structurally impossible and removes the
  reconciliation state machine entirely (R2).
- **Persistence: SQLite (WAL + busy_timeout).** Confirmed as the right store; DB choice is
  orthogonal to drift. `[AMENDED 2026-06-22]` Schema is two tables — `task` (keyed by `<task>` slug)
  + `task_worktree` (per-repo links) — not the original single branch-keyed `task_annotation` table.
- **One-active-task invariant:** "at most one running annotation per BRANCH" enforced by a
  partial unique index. The stronger "per worktree" form is read-time-observable only —
  an accepted, documented enforcement downgrade at single-dev scale.
- **Accepted v1 limitation:** `git branch -m` (rename) orphans an annotation; surfaced as a
  detected orphan in a labeled view, not built-for in v1.

### Technical Constraints & Dependencies

- Python + official MCP SDK (`mcp`); Streamable HTTP transport (D10) — NOT stdio.
- `[AMENDED 2026-06-22]` Long-lived, separately-launched process bound to 127.0.0.1; one instance
  **per machine** (global), registered once in Claude Code as an HTTP/URL MCP server shared across repos.
- Git via the `git` CLI; subprocess env pinned (`GIT_TERMINAL_PROMPT=0`,
  `GIT_OPTIONAL_LOCKS=0`, explicit `-C <repo>`/cwd); NUL-delimited (`-z`) porcelain parsing.
- No mature native async MCP task primitive relied upon (SEP-1391/1686 emerging only).

### Cross-Cutting Concerns Identified

- **Event-loop discipline:** every git/blocking call off-loop; subprocess concurrency
  bounded by a semaphore; per-command timeout + kill/reap (no zombies).
- **Derive-on-read purity:** the projection is a pure function of (live git, annotations)
  with NO write-back during a read; must be total (never throws on orphans).
- **git-unavailable failure mode:** graceful, labeled degradation if `git worktree list`
  fails — never a blank dashboard that reads as "no work."
- **Structured/typed error contract:** every reject path (path collision, branch collision,
  active-task conflict, locked worktree, invalid task name, timeout, instance conflict) is
  typed so tests assert on type, not strings; failed git ops leave the repo unchanged.
- **Destructive-op safety:** two distinct force flags; reconciliation/derive path NEVER
  calls a destructive git operation; a "what would be lost" preview (unmerged-commit count)
  before branch deletion.
- **Localhost security:** 127.0.0.1 bind + Origin validation across all routes.
- **Process lifecycle / single-instance:** clean detection (lockfile or port probe), no
  opaque EADDRINUSE; prints dashboard URL on startup.
- **`<task>` sanitization as an injection boundary:** validated against a pinned regex
  before any shell-out (it flows into both a path and a git ref); `--` end-of-options.
- **MCP tool-contract stability:** tool names + input/output shapes are a public contract.

> **PRD-fidelity note (revisit at the relevant decision):** FR-12 is worded around "state
> reconciled against actual git worktrees." Derive-on-read satisfies the *intent* (the
> dashboard never contradicts git) while changing the *mechanism* (read-time projection vs
> stored reconciliation). To reconcile explicitly in a later decision so no FR appears
> dropped.

## Starter Template Evaluation

### Primary Technology Domain

**Local backend service** — a Python MCP server (Streamable HTTP transport) co-hosting a
read-only web dashboard. Not a web-app/SPA domain, so conventional front-end starters
(Next.js, Vite, etc.) do not apply. There is no CLI "create-app" generator for Python MCP
servers; the foundation is a modern Python project scaffold plus the MCP SDK.

### Starter Options Considered

- **`uv init --package` (chosen scaffold).** uv is the 2026 default for Python project
  init/packaging. Produces `pyproject.toml`, `src/` layout (prevents accidental dev-code
  imports; tests run against the installed package), `uv.lock`, `uv_build` backend, git +
  `.gitignore`. Fast, single-tool, minimal.
- **Official `mcp` SDK — v1.28.0 (CHOSEN MCP framework).** Reference implementation of the
  wire protocol; bundles `FastMCP` (`mcp.server.fastmcp.FastMCP`) for decorator-based tools;
  supports Streamable HTTP; `streamable_http_app()` returns a mountable Starlette ASGI app.
  Requires Python ≥3.10. Brings Starlette/uvicorn transitively.
- **FastMCP standalone (PrefectHQ) — v3.x (CONSIDERED, NOT chosen).** Lowest-boilerplate,
  `@custom_route`, powers ~70% of servers. Rejected for v1: heavier, faster-moving
  third-party dependency that would OWN the HTTP server; for a multi-route dashboard +
  shared Origin middleware we want our app to own the HTTP surface, not bolt a dashboard
  onto FastMCP. Marginal ergonomic gain over the SDK's bundled FastMCP for a ~7-tool server.
- **Plain `mcp.run(transport="streamable-http")` (considered).** Fine for a pure tool
  server, but we must co-host dashboard routes + Origin middleware on the same process/port,
  which is cleaner via an owned Starlette app.

### Selected Foundation

**Scaffold:** uv (`uv init --package`, `src/` layout).
**MCP framework:** official `mcp` SDK v1.28.0 (bundled `FastMCP`).
**HTTP host:** an application-owned **Starlette** app that (a) registers the read-only
dashboard routes + `/state` endpoint, (b) applies Origin-validation middleware as the
OUTERMOST parent middleware (so it covers both dashboard routes AND the mounted MCP route),
and (c) mounts the SDK's `streamable_http_app()` at `/mcp`. Served by uvicorn, bound to
`127.0.0.1`.
**Persistence:** `aiosqlite` behind a single `Store` module (parameterized queries, WAL).

**Rationale for Selection:**
Stays on the reference implementation (boring, stable, wire-faithful) for the
security-sensitive transport/Origin work; the app-owned-Starlette + mount topology gives us
one middleware chokepoint over both surfaces; adds no web-framework dependency beyond what
the SDK already pulls in. Honors the single-long-lived-process topology the PRD/addendum require.

**Initialization Command (foundation, not frozen — pin versions at implementation time):**

```bash
uv init --package dev-helper-mcp
cd dev-helper-mcp
uv add "mcp>=1.28,<2"     # upper bound: official SDK v2 (stable 2026-07-27) has breaking changes
uv add "aiosqlite"        # event-loop-safe SQLite; Starlette+uvicorn arrive transitively via mcp
```

### Critical Wiring Notes (verified during Party Mode — carry into implementation)

- **SDK isolation seam:** ALL `mcp`/`FastMCP` contact lives in ONE adapter module
  (e.g. `server_factory.py`) that builds the FastMCP instance, registers tools, and returns
  the mountable ASGI app. Tool *logic* (async git ops, aiosqlite Store, derive-on-read)
  imports nothing from `mcp`. This is both the v2-migration insurance and the seam that makes
  tools unit-testable without a server.
- **Lifespan propagation (load-bearing):** Starlette does NOT auto-run a mounted sub-app's
  lifespan. The app-owned lifespan must wrap `async with mcp_app.lifespan(mcp_app):` or the
  StreamableHTTP session manager never starts and every `/mcp` request fails with
  "Task group is not initialized."
- **307-redirect fix (python-sdk #1168):** set `mcp.settings.streamable_http_path = "/"` and
  `Mount("/mcp", app=mcp.streamable_http_app())` so `/mcp` resolves at the mount point with no
  trailing-slash redirect. Clients connect to `http://127.0.0.1:<port>/mcp`.
- **Origin validation:** our own middleware (NOT FastMCP's `TransportSecurityMiddleware`,
  which the mounted-sub-app layout bypasses), attached as outermost parent middleware. Present
  + non-allowlisted Origin → 403; absent Origin (non-browser MCP client) → allow.

### Architectural Decisions Provided by This Foundation

- **Language & Runtime:** Python ≥3.10 (target 3.12+); uv-managed venv + `uv.lock`.
- **Packaging/Build:** `src/` layout, `uv_build` backend, `pyproject.toml`.
- **MCP framework:** official `mcp` SDK (bundled FastMCP); Streamable HTTP transport.
- **HTTP/dashboard host:** Starlette (transitive via SDK) + uvicorn; app-owned router mounts
  MCP at `/mcp` and serves dashboard routes + `/state`.
- **Persistence:** `aiosqlite` + single `Store` module; stdlib `sqlite3` semantics, WAL,
  parameterized queries; no ORM.
- **Dependency posture:** minimal — `mcp` (+ transitive Starlette/uvicorn) and `aiosqlite`.
  Honors NFR-Simplicity/Footprint.
- **Version risk captured:** `mcp` v2 (stable 2026-07-27) is a known breaking event; `<2`
  upper bound deliberate; ~Q4 2026 marker to re-check 1.x security-backport status. A v2
  migration is a tracked future decision, contained to the adapter module.
- **Test harness baseline:** `pytest` + `httpx.ASGITransport` (in-process, no port) for the
  Origin matrix + MCP tool-contract; one uvicorn real-port smoke test asserting bind to
  `127.0.0.1` (not `0.0.0.0`).
- **Code organization (detailed in later decisions):** layered — MCP tool/adapter layer →
  async git operations layer → aiosqlite Store → derive-on-read projection → dashboard.

**Note:** Project initialization using this command should be the first implementation story.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical (block implementation):** persistence model & schema; async-git execution contract;
derived-state cache + refresh model; error taxonomy; MCP tool/transport wiring (Step 3);
single-instance, port, and lockfile protocol.
**Important (shape architecture):** slug rules; DB location; dashboard refresh & presentation;
distribution.
**Deferred (post-MVP):** SSE server-push (polling v1); branch-rename safety net (accepted as
detectable orphan); multi-repo; CI pipeline; SQLite migration *runner* (version-check only now).

### Data Architecture

- **Engine/driver:** SQLite via `aiosqlite`, behind one `Store` module; parameterized queries
  only; no ORM. Explicit `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout` at bootstrap (a
  polling dashboard reads while tools write — WAL avoids `SQLITE_BUSY`).
- **DB location `[AMENDED 2026-06-22]`:** **machine-global** at
  `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db` (alongside `server.lock`) — NOT
  in-repo, because one server spans repos. Worktrees still live in each repo's sibling dir.
- **Schema (two tables — `[AMENDED 2026-06-22]` task is first-class, owns per-repo worktree links):**
  ```sql
  CREATE TABLE IF NOT EXISTS task (
    task_id       TEXT PRIMARY KEY,                  -- the <task> slug; one active task per slug
    description   TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('running','blocked','review','done')), -- [AMENDED 2026-06-22b] +review (blocked=awaiting input, review=awaiting review)
    created_at    TEXT NOT NULL,                     -- UTC ISO-8601
    updated_at    TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS task_worktree (
    task_id       TEXT NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
    repo_path     TEXT NOT NULL,                     -- absolute repo path
    branch        TEXT NOT NULL,                     -- agent/<task> (same slug across this task's repos)
    worktree_path TEXT NOT NULL,
    PRIMARY KEY (task_id, repo_path)                 -- one worktree per repo per task
  );
  ```
  `PRAGMA foreign_keys=ON` at bootstrap so the cascade holds. Re-tasking a slug after `done` is an
  **UPSERT** on `task` (overwrite description/status, preserve `created_at`, advance `updated_at`)
  plus a refresh of its `task_worktree` rows. No task history in v1 (YAGNI).
- **Source of truth:** git `worktree list --porcelain` **per tracked repo** for worktree existence;
  the tables hold ONLY task records + their per-repo worktree linkage.
- **Migrations:** **version-check only** — store `PRAGMA user_version`; on startup refuse to
  open a DB from a *newer* version with a clear error; otherwise `CREATE TABLE IF NOT EXISTS`.
  No migration runner until a real v2 schema exists.
- **Orphaned-link rule (the core derive-on-read consistency rule) `[AMENDED 2026-06-22]`:** a
  `task_worktree` link whose `branch` is absent from its repo's `git worktree list` is **shown,
  flagged as orphaned, NEVER auto-deleted and never auto-`done`** — a git operation must never
  silently eat the non-derivable task records we store. A task all of whose links are orphaned is
  surfaced as a fully-orphaned task, not dropped.

### Derived State & Refresh Model

- **Derive-on-read into an in-memory cache.** The repo×worktree×task view is a pure projection
  of (live git `worktree list --porcelain` × annotations). It is computed into an **in-memory**
  cache, refreshed on a **background tick** and on every mutating tool call. The cache is
  ephemeral (rebuilt from scratch on restart) — there is NO persisted derived state, so
  permanent git↔DB drift remains structurally impossible; worst-case staleness is bounded by
  the tick and self-heals.
- `/state` is served from this cache — it **never shells out to git on a poll**.
- Cache payload carries `generated_at`; if a refresh fails (git unavailable), the cache keeps
  last-known state and marks it stale rather than going blank.

- **Cached view shape (pinned — the ONLY shape `/state` and the refresh tick share) `[AMENDED
  2026-06-22]` — grouped by task across repos:**
  ```
  CacheSnapshot:
    generated_at: str              # now_iso()
    tasks:        list[TaskView]       # sorted by task_id ASC (stable order)
    warnings:     list[str]            # non-fatal notes, e.g. "orphan_link:<task_id>@<repo>:<branch>"; [] when clean
  TaskView:
    task_id:     str               # the <task> slug
    description: str | None        # annotation; None only if a worktree exists with no task (task-less)
    status:      str | None        # task status; None when task-less
    created_at:  str | None
    updated_at:  str | None
    worktrees:   list[WorktreeView]    # one per repo this task touches; sorted by repo_path ASC
  WorktreeView:
    repo_path:   str               # which repo (absolute)
    branch:      str               # agent/<task>; join key within the repo
    path:        str | None        # absolute worktree path; None for a link-only orphan
    head:        str | None        # short sha
    detached:    bool
    locked:      bool              # porcelain 'locked'
    prunable:    bool              # porcelain 'prunable'
    orphaned:    bool              # link present in DB, branch absent from this repo's porcelain
  ```
  **Join rule:** for each tracked repo, porcelain is the existence set; `task_worktree` links
  LEFT-JOIN on `(repo_path, branch)`. A worktree present in git with no link → emitted under a
  synthetic task-less entry (or a dedicated `untracked` bucket). A link whose branch is absent from
  its repo's porcelain → emitted with `orphaned: true` AND noted in `warnings` as
  `orphan_link:<task_id>@<repo>:<branch>`. The snapshot is **immutable and swapped whole** (never
  mutated in place).

- **Mutation critical-section ordering (pinned — a tool never returns `ok` on stale state):**
  ```
  1 acquire mutation-pool slot
  2 run_git mutation (mutation pool)
  3 on git ok: UPSERT annotation (aiosqlite)
  4 rebuild snapshot: read porcelain (read pool) + SELECT all annotations → project
  5 atomically swap cache ref to the new snapshot   # single ref assignment (GIL-atomic)
  6 release slot
  7 return envelope (data = the just-built snapshot, or the affected WorktreeView)
  ```
  Refresh (step 5) is **inside** the mutation's critical section, before the return (step 7).
  Read-only tools and `/state` read `cache.current` by ref — no lock, never a torn snapshot.
  The background tick performs the same 4→5 rebuild; because each rebuild is a full snapshot,
  last-writer-wins on the ref is correct (no partial merge).

### Authentication & Security

- **No auth** (single-user localhost trust model).
- **Bind `127.0.0.1`** only — never `0.0.0.0` (asserted by a smoke test).
- **Origin-validation middleware** (our own, outermost parent middleware over `/mcp` + all
  dashboard routes): `Origin` present & not in `{http://127.0.0.1:<port>, http://localhost:<port>}`
  → 403; absent (non-browser MCP client) → allow. DNS-rebinding defense.
- **Input safety:** task names validated against a pinned regex before any shell-out; git via
  `exec` (no shell) with `--` end-of-options against argument injection.

### API & Communication Patterns

- **Transport:** MCP Streamable HTTP via official SDK, mounted at `/mcp` (Step 3 wiring).
- **Tool surface (5) `[AMENDED 2026-06-22]`:** `create_task` (creates the task + one worktree per
  supplied repo, **all-or-nothing**; replaces the old `create_worktree`+`register_task` pair),
  `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks` (final names/schemas pinned in
  patterns step). `create_task` inputs: `task_name`, `description`, `repos: [abs_path,…]` (1+),
  `base_ref?` (default each repo's HEAD); output `{task_id, status, worktrees:[{repo_path,
  worktree_path, branch}]}`.
- **`create_task` atomicity & rollback (AR-13) `[AMENDED 2026-06-22, decided 2026-06-22]`:**
  - **Preflight first:** validate ALL repo paths (git repo? `agent/<task>` branch/dir free? base ref
    exists?) before mutating ANY repo — the cheapest rollback is not starting (`NotAGitRepo`,
    `BranchExists`, `WorktreePathInUse`, `BaseRefNotFound` raised before any `worktree add`).
  - **Ordering:** provision all worktrees first; write the `task` + `task_worktree` rows **LAST**, in a
    single SQLite transaction, only on full success. A crash before the commit leaves NO DB rows.
  - **Error-safe (REQUIRED):** if a `worktree add` fails after earlier repos succeeded, compensate —
    `git worktree remove --force` + `git branch -D agent/<task>` for each already-created repo — and
    write no rows. Teardown order is the reverse of creation; each compensation result is captured.
  - **`RollbackIncomplete`:** if a compensating teardown itself fails, do NOT swallow it — return
    `RollbackIncomplete` whose `details` names the repo paths left orphaned, preserving the original
    cause. Silent partial state is forbidden.
  - **Crash-safety = documented v1 NON-GOAL:** a SIGKILL/OOM mid-`create_task` may leave an orphaned
    worktree/branch (no DB rows, per ordering). It is **surfaced** by derive-on-read as an untracked
    worktree on the dashboard and re-collides (`BranchExists`) on a same-name retry — visible and
    recoverable, never silent corruption. No startup reconciliation sweep is built in v1 (keeps the
    deliberately-designed-out reconciliation engine out).
- **Per-repo mutation mutex (concurrency safety) `[AMENDED 2026-06-22, decided 2026-06-22]`:** the
  global lockfile guards the **process singleton**, NOT per-repo mutation safety. Because two
  concurrent `create_task`/`remove_worktree` calls may touch the **same repo** (e.g. `repos=[x,y]`
  and `[y,z]`) and the mutation pool (sem=4) is a concurrency *limiter* not a mutual-exclusion lock,
  an in-process **async mutex keyed by `repo_path`** serializes mutations to the same repo. Read/refresh
  git ops do not take it. (Without this, concurrent same-repo `git worktree add` races — a data-loss
  path Murat flagged.)
- **Async-git execution — TWO latency classes, separate permit pools (the ≤3s SLA fix):**
  - *Read/refresh class* (`worktree list`, status, unreachable-commit count): **3s** per-command
    timeout, pool **semaphore=2**, **2s acquire timeout** (fail fast / keep cache rather than
    queue). Feeds the cache; never on the poll path.
  - *Mutation class* (`worktree add`/`remove`, `branch -d/-D`): **generous bounded** timeout
    (≈120s, configurable — `git worktree add` checkout can be legitimately multi-second on a
    large/cold repo; stays well under the ~5-min transport ceiling), pool **semaphore=4**.
  - Both: off the event loop via `create_subprocess_exec`; on timeout `kill()` + `await wait()`
    (no zombies); both pipes drained; env pinned (`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`,
    `-C <repo>`); `-z` NUL-delimited porcelain parsing; failed git ops leave the repo unchanged.
- **Slug rules (`<task>`) `[AMENDED 2026-06-22]`:** lowercase, hyphenate, collapse
  duplicate/leading/trailing hyphens, **max length 60**; reject empty/reserved/`.`/`..`; the slug is
  the `task_id` and the `agent/<task>` branch name used in **every** repo the task spans; collision
  (branch OR target dir exists in **any** requested repo) → structured reject, create is
  all-or-nothing across repos (no silent suffixing).
- **Error taxonomy (typed, stable `code`):** `BranchExists`, `WorktreePathInUse`,
  `BaseRefNotFound`, `DirtyWorktree`, `UnmergedBranch`, `TaskNotFound`, `ActiveTaskConflict`,
  `LockedWorktree`, `InvalidTaskName`, `GitTimeout`, `InstanceConflict` (`[AMENDED 2026-06-22]`
  reserved for **same-machine**-already-running with a live, identity-matched pid), `NotAGitRepo`
  (`[AMENDED 2026-06-22]` a supplied repo path is not a git repository), `RollbackIncomplete`
  (`[AMENDED 2026-06-22]` a `create_task` compensating teardown itself failed — `details` lists the
  repo paths left orphaned; the original cause is preserved), `PortUnavailable` (explicit `--port`
  bind failed). Each `{code, message, details}`.
- **Removal force-flag semantics (two distinct guards — distinct blast radii, hence two flags):**
  `force` → dirty/locked *worktree* removal (`git worktree remove --force`); `force_unmerged_branch`
  → *branch* deletion with unmerged commits (`git branch -D` vs `-d`), surfacing the
  unreachable-commit count first. The read/refresh path NEVER calls a destructive git op.

### Frontend Architecture (dashboard)

- **Read-only** Starlette routes + a `/state` JSON endpoint (served from the in-memory cache);
  no mutating routes.
- **Refresh:** client polls `/state` every ~1–2s. Worst-case freshness = tick interval + poll
  interval, kept within the ≤3s soft SLO `[AMENDED 2026-06-22]` **bounded to ≤15 tracked repos**
  (beyond that the per-repo `git worktree list` fan-out under the read pool — sem=2, 3s timeout, 2s
  acquire — can exceed the budget; the SLO is explicitly conditional, not unbounded).
- **Per-repo fan-out degradation (decided 2026-06-22):** the refresh fans `git worktree list` across
  every tracked repo via the read pool. A slow/timed-out repo **degrades that repo only** — its
  worktrees render as "unavailable"/last-known while the rest of the board renders normally. One slow
  (e.g. monorepo / networked) repo MUST NOT blank or fail the whole dashboard.
- **Presentation guardrails (protect the <10s glance / SM-2):**
  - `generated_at` rendered **subordinate** (small, cornered; greys/ambers when stale) — proof
    of freshness, not a headline.
  - **Orphaned-notes section demoted out of the primary glance path** (collapsed / below-fold);
    label is **self-explaining** ("branch gone — note preserved here").
  - **Render is stable across polls** — DOM changes only when *state* changes (no per-poll
    animation/reflow).
- **git-unavailable degradation:** show labeled last-known state, never a blank dashboard.
- **UI:** minimal server-rendered HTML + a tiny vanilla-JS poller; no SPA/build tooling. `[AMENDED
  2026-06-22b]` The visual + experience contract is the **UX spec** at
  `ux-designs/ux-dev-helper-mcp-2026-06-22/` (`DESIGN.md` tokens + `EXPERIENCE.md` IA/states/flows,
  reference mock `mockups/key-screen-board.html`): a dark, compact "modern console" board of **three
  active status columns** (Running | Blocked | Review) with `done` as a foldable `✓ N done` count
  below, status triple-encoded (column + left bar + per-card glyph), **blocked-emphasized** (the only
  lifted card), **no motion**, **diff-and-patch poller** (stable render), self-contained (no external
  assets). UX-DR1–13 are enumerated there and carried in the epics.

### Infrastructure & Deployment

- **Process `[AMENDED 2026-06-22]`:** single long-lived **machine-global** process, started by the
  developer (`dev-helper-mcp [--port N]` — **no `--repo`**), serving MCP + dashboard for all repos
  from one event loop; prints the dashboard URL on startup. Repos are discovered per task (absolute
  path in `create_task`), validated as git repos before use.
- **Port:** default scan **8765→8775**, bind first free; `--port N` is a **strict override**
  (bind N or fail with `PortUnavailable`). The actual bound port is written to the lockfile and
  printed — the dashboard reads the lockfile, never a hardcoded constant.
- **Single-instance + lockfile `[AMENDED 2026-06-22]` (machine-global
  `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/server.lock`, `{pid, port, start_ts}`) — one
  instance per machine, not per repo:**
  - **Atomic create** via `os.open(O_CREAT|O_EXCL)` (serializes concurrent starts — kills TOCTOU).
  - On `EEXIST`, **stale check**: pid liveness (`os.kill(pid,0)`) **plus** an identity guard
    against pid reuse; dead/unrelated → **atomic-rename takeover**. The identity guard is
    **Linux-first** (`/proc/<pid>` start-time / `boot_id`); on non-Linux it **degrades to
    pid-liveness only + a startup warning** — acceptable because the port-bind mutex below is
    authoritative regardless (matches NFR-Portability: Linux primary, macOS best-effort).
  - **The port bind is the authoritative mutex** — `EADDRINUSE` ⇒ `InstanceConflict` regardless
    of lock state (makes pid-reuse false positives non-fatal).
  - Released on clean shutdown (atexit + signal handler); stale tolerance covers the unclean path.
  - `dev-helper-mcp stop` / `--release-lock` provided so nobody reaches for `rm -rf`.
- **Distribution:** `console_scripts` entry point `dev-helper-mcp`, installed via
  `uv tool install` (or pipx). `src/` layout, `uv_build` backend.
- **Logging/observability:** stdlib `logging` to stderr; level via env (`DEV_HELPER_LOG`).
- **CI/CD:** none for v1 (solo tool), BUT the gate is **enforced via a pre-commit hook** running
  `ruff check`, `ruff format --check`, and `pytest` (in-process suite; the uvicorn real-port
  smoke test may be slow-marked / opt-in). Enforcement — not "local discipline" — is what replaces
  CI's regression gate. The suite is CI-ready if contributors join.

### Decision Impact Analysis

**Implementation sequence:**
1. uv scaffold + `pyproject` + entry point + pins (`mcp>=1.28,<2`, `aiosqlite`).
2. SDK adapter/`server_factory` (mount `/mcp`, lifespan, Origin middleware) + Origin/transport tests.
3. `Store` (aiosqlite, schema, UPSERT, version-check) + slug validation + error taxonomy.
4. Async-git layer: read/refresh + mutation pools, timeouts, `-z` porcelain parsing.
5. `[AMENDED]` Task-centric tools: `create_task` (multi-repo, all-or-nothing) + `list_worktrees`/`remove_worktree` + `update_task`/`list_tasks`.
6. Derived-state cache (multi-repo fan-out, grouped by task) + background refresher + `/state` + dashboard UI/polling + presentation guardrails.
7. `[AMENDED]` Machine-global single-instance lockfile protocol + port auto-fallback + CLI (`--port`, `stop` — no `--repo`) + startup URL.

**Cross-component dependencies:**
- Origin middleware sits above BOTH the MCP mount and dashboard routes (one chokepoint).
- The cache depends on the async-git read pool + Store; the refresher (not the poll) drives git.
- Lockfile + DB share `.dev-helper-mcp/`; the lock is reclaimable, the DB is durable.
- The SDK adapter seam isolates the `mcp` dependency for the tracked v2 migration.

### PRD-Fidelity Gate (FR-12)

FR-12 originally read "state reconciled against actual git worktrees." The mechanism is now
**derive-on-read into an ephemeral cache** (read-time projection), which satisfies and
*strengthens* the intent (the dashboard never contradicts git; no stored copy to drift).
**Resolution (I-1 — CLOSED):** the PRD's FR-12 should be annotated with this changelog note so
the mechanism change reads as an intentional improvement, not a dropped requirement:

> *FR-12 changelog (2026-06-19): mechanism changed from stored reconciliation to **read-time
> projection (derive-on-read)**; intent — "the dashboard never shows a state that contradicts
> git" — is unchanged and strengthened (no stored copy can drift). Existence truth =
> `git worktree list --porcelain`; the DB holds only branch-keyed task annotations.*

This note is the authoritative reconciliation of FR-12 with the architecture; apply it to the PRD
text when convenient (a pure traceability edit, non-blocking for implementation).

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

These rules pin the points where independent agents could each make a locally-reasonable but
divergent choice. They govern HOW to implement, not WHAT. All are mandatory unless marked
"guideline."

### Naming Patterns

**Python code:**
- `snake_case` for functions, variables, modules; `PascalCase` for classes; `UPPER_SNAKE` for
  module-level constants. Type hints on every public function signature.
- Private helpers prefixed `_`. One concept per module; module names are nouns
  (`store.py`, `git_ops.py`, `cache.py`, `lock.py`, `errors.py`, `server_factory.py`, `cli.py`).

**MCP tools:**
- Tool names are `snake_case`, verb-first `[AMENDED 2026-06-22]`: `create_task`, `list_worktrees`,
  `remove_worktree`, `update_task`, `list_tasks` (5 tools; `create_task` subsumes the old
  `create_worktree`+`register_task`).
- Tool input/output JSON fields are `snake_case` (`task_name`, `base_ref`, `worktree_path`,
  `task_id`, `created_at`).

**Database:**
- Table/column names `snake_case` (`task`, `task_worktree`, `created_at`). Status literals lowercase
  (`running`/`blocked`/`review`/`done`) `[AMENDED 2026-06-22b]`.

### Data & Format Patterns

- **JSON casing — `snake_case` everywhere** (tool I/O AND `/state`). No case-translation layer;
  the dashboard JS reads `snake_case` keys.
- **Timestamps — UTC ISO-8601 with `Z`, second precision** (`2026-06-19T13:18:25Z`), produced
  by a single `now_iso()` helper. Never local time, never bare epoch ints.
- **Booleans** as JSON `true`/`false`; **absent/optional** values omitted or `null` consistently
  (prefer omission in tool outputs, explicit `null` only when "unknown" is meaningful).
- **Tool input modeling — Pydantic models at the boundary** (one `*In` model per tool, e.g.
  `CreateWorktreeIn`). Validation + advertised JSON schema come from the model. Core logic
  functions take plain typed args, not the Pydantic model (keeps logic SDK-agnostic).

### Result Envelope & Error Patterns

- **Every tool returns a uniform envelope:**
  ```json
  { "ok": true,  "data": { ... } }
  { "ok": false, "error": { "code": "BranchExists", "message": "...", "details": { ... } } }
  ```
- **Errors are DATA, not protocol failures.** Core logic raises a typed `DevHelperError`
  (subclass per `code`); the tool-adapter layer catches it and returns `{ok:false, error:{...}}`.
  The agent branches on `error.code`. Unexpected (non-`DevHelperError`) exceptions are caught at
  the adapter, logged, and returned as `{ok:false, error:{code:"Internal", ...}}` — a tool never
  leaks a raw stack trace to the client.
- **`error.code`** is from the fixed Step-4 taxonomy (`BranchExists`, `WorktreePathInUse`,
  `BaseRefNotFound`, `DirtyWorktree`, `UnmergedBranch`, `TaskNotFound`, `ActiveTaskConflict`,
  `LockedWorktree`, `InvalidTaskName`, `GitTimeout`, `InstanceConflict`, `NotAGitRepo`,
  `RollbackIncomplete`, `PortUnavailable`, `Internal`). `[AMENDED 2026-06-22]` `NotAGitRepo` and
  `RollbackIncomplete` added for multi-repo. Codes are stable contract; messages may change.

### Structure & Process Patterns

- **Git invocation — exactly one `run_git()` helper.** No ad-hoc `subprocess`/`os.system`
  anywhere else. It always uses `create_subprocess_exec` (never shell), always passes
  `-C <repo>`, uses `-z` NUL parsing where it parses output, pins the env
  (`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`), and is called via the correct pool:
  **read/refresh pool** (3s timeout, sem=2) vs **mutation pool** (≈120s, sem=4).
- **Async discipline — no blocking call on the event loop, ever.** All git through `run_git()`;
  all DB through `aiosqlite`; any unavoidable sync work via `asyncio.to_thread`.
- **Derive-on-read purity** — the projection function is pure `(git_listing, annotations) →
  view`; it performs NO writes and NO destructive git ops. The refresher writes the in-memory
  cache; the projection itself does not.
- **Tests** — `tests/` mirrors `src/` module layout; files `test_<module>.py`; prefer the
  in-process `httpx.ASGITransport` harness for HTTP/tool tests, temp/`:memory:` DB for Store
  tests. (Full tree is defined in the next step.)
- **Logging** — `logging.getLogger(__name__)` per module; level from `DEV_HELPER_LOG`
  (default `INFO`); one line per event to stderr; never log secrets (there are none) or full
  annotation contents at `INFO`.

### Enforcement Guidelines

**All agents MUST:**
- Route every git call through `run_git()` and the appropriate pool — never call `git` directly.
- Return the `{ok, data, error}` envelope from every tool; raise `DevHelperError` (never return
  ad-hoc error dicts) in core logic.
- Use `snake_case` JSON keys and the `now_iso()` timestamp helper.
- Keep core logic free of `mcp`/`starlette` imports (only the adapter layer touches the SDK).
- Add no blocking I/O on the event loop.

**Enforcement:** `ruff` (lint + format) is the mechanical gate; `pytest` asserts the contract
(envelope shape, error codes, snake_case keys, 127.0.0.1 bind, Origin matrix). Pattern changes
are made here in the architecture doc first, then propagated — not invented ad-hoc in a story.

### Examples

**Good:**
```python
# tool adapter  [AMENDED 2026-06-22: task-centric, multi-repo]
async def create_task(inp: CreateTaskIn) -> dict:
    try:
        result = await tasks.create(inp.task_name, inp.description, inp.repos,
                                     base_ref=inp.base_ref)  # plain args; all-or-nothing across repos
        return {"ok": True, "data": result}
    except DevHelperError as e:
        return {"ok": False, "error": e.as_dict()}   # {code, message, details}
```

**Anti-patterns (reject in review):**
- A handler calling `subprocess.run(["git", ...])` directly (bypasses pool, timeout, env).
- A tool returning bare `{"path": ...}` on success but raising on failure (non-uniform shape).
- `datetime.now()` (local, no `Z`) or epoch ints in JSON.
- `camelCase` JSON keys; a translation layer.
- Core logic importing `from mcp...` (breaks the v2-migration seam).

## Project Structure & Boundaries

### Complete Project Directory Structure

```
dev-helper-mcp/
├── README.md
├── pyproject.toml                 # project meta, deps (mcp>=1.28,<2; aiosqlite), entry point, ruff config
├── uv.lock
├── .python-version                # 3.12
├── .gitignore                     # includes .dev-helper-mcp/
├── src/
│   └── dev_helper_mcp/
│       ├── __init__.py
│       ├── __main__.py            # `python -m dev_helper_mcp` → cli.main()
│       ├── cli.py                 # arg parsing: --port, `stop`/--release-lock; dispatch (NO --repo)
│       ├── config.py              # Settings/constants: DEFAULT_PORT=8765, PORT_RANGE, timeouts,
│       │                          #   pool sizes, GLOBAL paths (XDG_STATE_HOME/dev-helper-mcp/{state.db,server.lock})
│       ├── errors.py              # DevHelperError base + per-code subclasses; .as_dict() → {code,message,details}
│       ├── util.py                # now_iso() and other tiny pure helpers
│       │
│       │   # ── Adapter / presentation layer (ONLY place that imports mcp / starlette) ──
│       ├── server_factory.py      # build FastMCP, register tools, set streamable_http_path="/",
│       │                          #   own Starlette app: Origin middleware (outermost) + dashboard
│       │                          #   routes + Mount("/mcp"); lifespan wraps mcp_app.lifespan
│       ├── server.py              # lifecycle: port auto-fallback bind, acquire lock, run uvicorn
│       │                          #   on 127.0.0.1, print dashboard URL, graceful shutdown
│       ├── middleware.py          # OriginValidationMiddleware (allowlist 127.0.0.1/localhost:port)
│       ├── lock.py                # lockfile protocol: O_EXCL create, stale-reclaim (pid+identity),
│       │                          #   atomic-rename takeover, release on shutdown
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── models.py          # Pydantic *In models (CreateTaskIn{task_name,description,repos[],base_ref?}, UpdateTaskIn, …)
│       │   └── handlers.py        # tool adapters: validate → call core → {ok,data,error} envelope
│       ├── dashboard/
│       │   ├── __init__.py
│       │   ├── routes.py          # GET / (HTML), GET /state (JSON from cache) — read-only
│       │   └── static/
│       │       ├── index.html
│       │       ├── app.js         # poll /state ~1–2s; stable render; subordinate generated_at
│       │       └── style.css      # 4 status columns/badges (running/blocked/review/done); demoted orphan section
│       │
│       │   # ── Core logic layer (NO mcp / starlette imports — the v2-migration seam) ──
│       ├── core/
│       │   ├── __init__.py
│       │   ├── worktrees.py       # per-repo create/list/remove logic; plain args; raises DevHelperError
│       │   ├── tasks.py           # create_task (multi-repo, all-or-nothing)/update/list; UPSERT; task+task_worktree
│       │   └── slug.py            # task-name validation + slugify (pinned regex, max 60, reject rules)
│       ├── git/
│       │   ├── __init__.py
│       │   ├── runner.py          # run_git(): create_subprocess_exec, read-pool vs mutation-pool,
│       │   │                      #   timeouts, kill+reap, pinned env, -C <repo>
│       │   └── porcelain.py       # parse `git worktree list --porcelain -z`
│       ├── store.py               # aiosqlite Store: WAL bootstrap, user_version check,
│       │                          #   CREATE TABLE IF NOT EXISTS, UPSERT, queries
│       ├── projection.py          # PURE derive-on-read: (git_listing, annotations) → view; no writes
│       └── cache.py               # in-memory cache + background refresher tick (drives git via read-pool)
└── tests/
    ├── conftest.py                # fixtures: tmp git repo, in-process ASGITransport client, temp DB
    ├── fixtures/
    │   └── porcelain/             # sample --porcelain outputs: detached HEAD, locked, prunable, unicode
    ├── test_server_factory.py     # mount works, lifespan starts session mgr, /mcp no 307
    ├── test_middleware_origin.py  # Origin matrix on /mcp AND /state (403/allow)
    ├── test_smoke_uvicorn.py      # real ephemeral port; asserts bind 127.0.0.1 not 0.0.0.0
    ├── test_store.py              # two-table schema + FK cascade, UPSERT, version-check, WAL
    ├── test_git_runner.py         # timeout→kill+reap, pool bounds, acquire timeout, env
    ├── test_porcelain.py          # fixture corpus parse
    ├── test_worktrees.py          # per-repo create/list/remove + error codes
    ├── test_tasks.py              # create_task multi-repo + all-or-nothing rollback + NotAGitRepo + update/list + status CHECK + one-active-per-slug
    ├── test_slug.py               # valid/invalid names, collision reject
    ├── test_projection.py         # purity (no writes), orphan detection, idempotent view, multi-repo grouping by task
    ├── test_cache.py              # refresh tick, stale generated_at on git-unavailable, per-repo degrade (slow repo → that repo "unavailable", board still renders)
    ├── test_perf_fanout.py        # [AMENDED] param (num_tasks, repos_per_task); slow-repo injector (asyncio.sleep); assert p95 derive ≤3s for ≤15 repos; 2s-acquire cliff under concurrent readers
    ├── test_concurrency.py        # [AMENDED] per-repo mutex serializes same-repo create_task/remove; two create_task touching shared repo don't race
    ├── test_lock.py               # O_EXCL, stale takeover (dead PID reclaim / live PID refuse), pid-reuse, concurrent-start, port mutex
    ├── test_tools.py              # uniform envelope shape, error-as-data, snake_case keys
    └── test_cli.py                # --port/stop dispatch (no --repo), port auto-fallback
```

### Architectural Boundaries

**Layer boundary (the load-bearing one):**
- **Adapter/presentation** (`server_factory`, `server`, `middleware`, `tools/`, `dashboard/`,
  `lock`, `cli`) — the ONLY code allowed to import `mcp` or `starlette`/`uvicorn`. This isolates
  the SDK for the tracked v2 migration.
- **Core logic** (`core/`, `git/`, `store`, `projection`, `cache`, `errors`, `util`) — imports
  nothing from `mcp`/`starlette`; takes plain args, returns plain data, raises `DevHelperError`.
  Independently unit-testable with no server running.

**API boundary:** one external surface = the Starlette app on `127.0.0.1`. `/mcp` (MCP
Streamable HTTP, mounted) + dashboard routes `/`, `/state`. Origin middleware sits above both as
the single security chokepoint. No other listeners.

**Data boundary:** `store.py` is the ONLY module that opens the SQLite DB; `git/runner.py` is the
ONLY module that spawns `git`. `projection.py` consumes their outputs and is pure (no I/O).
`cache.py` is the ONLY writer of the in-memory derived view.

**Tool boundary:** `tools/handlers.py` is the adapter seam — it validates (Pydantic `*In`), calls
core logic, and wraps results in the `{ok, data, error}` envelope. Core logic never builds the
envelope; handlers never contain git/DB logic.

### Requirements → Structure Mapping

| FR group | Lives in |
|---|---|
| **FR-1–3** Worktree management `[AMENDED]` | `core/worktrees.py` (per-repo), `core/tasks.py` (create_task orchestrates across repos), `git/runner.py` (`-C <repo>`), `git/porcelain.py`, `core/slug.py`, `tools/handlers.py` |
| **FR-4–7** Per-agent task tracking `[AMENDED]` | `core/tasks.py` (task + task_worktree, multi-repo), `store.py` (two tables), `tools/handlers.py`, `tools/models.py` |
| **FR-8–10** Live dashboard | `dashboard/`, `cache.py`, `projection.py` |
| **FR-11** MCP tool surface | `server_factory.py`, `tools/` |
| **FR-12** Persistence + git consistency | `store.py`, `projection.py`, `cache.py` (derive-on-read) |
| **FR-13** Server lifecycle / single-instance | `server.py`, `lock.py`, `cli.py`, `config.py`, `middleware.py` |

**Cross-cutting concerns:**
- *Security (Origin + 127.0.0.1):* `middleware.py` + `server.py` bind.
- *Errors:* `errors.py` (taxonomy) consumed by all core logic, surfaced by `tools/handlers.py`.
- *Async/git discipline:* `git/runner.py` (the single `run_git()` + pools).
- *Config/constants:* `config.py`.

### Integration Points & Data Flow

**Tool call (mutation, e.g. create_task across N repos) `[AMENDED 2026-06-22]`:**
`Claude Code → /mcp → FastMCP → tools/handlers → core/tasks → (for each repo) git/runner (mutation
pool) worktree add + store UPSERT task & task_worktree rows → cache.refresh() → envelope back to
agent. All-or-nothing: on any repo failing, roll back already-created worktrees and the task rows.`

**Dashboard poll (read path, never touches git):**
`browser → GET /state → dashboard/routes → reads cache (in-memory) → JSON {…, generated_at}.`

**Background refresh `[AMENDED 2026-06-22]`:**
`cache tick → for each distinct repo in task_worktree: git/runner (read pool, ` + "`git -C <repo> worktree list --porcelain -z`" + `) + store.read_all() →
projection.derive() (group by task across repos) → atomically replace in-memory cache.`

**External integrations:** none beyond the local `git` binary. No network egress, no telemetry.

### File Organization Patterns

- **Config:** all tunables in `config.py` (one source); no magic numbers scattered in modules.
- **Source:** `src/` layout, one concept per module, layered as above.
- **Tests:** `tests/` mirrors modules; `test_<module>.py`; shared fixtures in `conftest.py`;
  git-output corpus in `tests/fixtures/porcelain/`.
- **Static assets:** `dashboard/static/` (shipped inside the package; served by Starlette).
- **Runtime state (NOT in the package) `[AMENDED 2026-06-22]`:** machine-global
  `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/{state.db, server.lock}` — created at runtime per
  machine (not per repo), never inside `src/`. (Worktrees still live as siblings of each repo.)

### Development Workflow Integration

- **Dev run `[AMENDED 2026-06-22]`:** `uv run dev-helper-mcp` (no `--repo`; the server is global and
  learns repos from `create_task`). Optionally `--port N`. (or `uv run python -m dev_helper_mcp`).
- **Build/dist:** `uv build` (uv_build backend, `src/` layout); install via `uv tool install`.
- **Quality gate (local):** `ruff check`, `ruff format --check`, `pytest` (in-process ASGI
  harness + the one real-port smoke test).
- **Deployment `[AMENDED 2026-06-22]`:** none — the developer installs the console tool and runs one global process per machine.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
All technology choices are mutually compatible. `mcp>=1.28,<2` (bundled FastMCP), `aiosqlite`
(WAL), and an application-owned Starlette host coexist without conflict; the `<2` pin deliberately
fences the known 2026-07-27 breaking SDK release and is contained to the adapter seam. No
contradictory decisions exist — the two-pool async-git model, derive-on-read cache, and
`{ok,data,error}` envelope reinforce one another rather than compete. The 11 Invariants are
internally consistent (derive-on-read ↔ "never persist derived state" ↔ "/state reads cache only";
single `run_git()` ↔ two-pool latency classes; SDK-isolation seam ↔ adapter/core layer split).

**Pattern Consistency:**
Implementation patterns support every decision. `snake_case` JSON is enforced end-to-end (tool I/O
AND `/state`) with no translation layer; the single `run_git()` and `now_iso()` helpers centralize
the two highest-divergence-risk operations; the typed `DevHelperError` → `{ok,false,error}` path is
uniform across all tools. Naming conventions (modules as nouns, verb-first tool names, lowercase
status literals) are consistent across code, tools, and the DB schema.

**Structure Alignment:**
The project structure enforces the load-bearing layer boundary: only the adapter layer
(`server_factory`, `server`, `middleware`, `tools/`, `dashboard/`, `lock`, `cli`) imports
`mcp`/`starlette`; `store.py` is the sole DB opener; `git/runner.py` is the sole git spawner;
`projection.py` is pure (no I/O); `cache.py` is the sole writer of the in-memory view. This
structure directly enables the chosen patterns (v2-migration seam, single-writer cache, one
security chokepoint) and the integration points are explicit in the data-flow section.

### Requirements Coverage Validation ✅

**Feature Coverage:**
All 5 feature areas — Worktree Management, Per-Agent Task Tracking, Live Web Dashboard, MCP Server
& Tool Surface, State Persistence & Lifecycle — have explicit module homes in the FR→Structure map.

**Functional Requirements Coverage:**
FR-1 through FR-13 each map to concrete modules via the Requirements → Structure table. FR-12's
mechanism change (stored reconciliation → derive-on-read) is explicitly reconciled and *strengthened*
in the PRD-Fidelity Gate (FR-12): the intent — "the dashboard never shows a state that contradicts
git" — is preserved, and no FR is dropped.

**Non-Functional Requirements Coverage:**
- *Performance:* two-pool model with a 3s read timeout protects the ≤3s soft read SLA `[AMENDED
  2026-06-22]` **bounded to ≤15 tracked repos**; per-repo fan-out degrades a slow repo individually
  rather than failing the aggregate. Mutation pool bounded well under the ~5-min transport ceiling.
- *Security/Locality:* 127.0.0.1 bind (smoke-tested) + outermost Origin-validation middleware over
  `/mcp` and dashboard routes (DNS-rebinding defense).
- *MCP-Compatibility:* protocol-synchronous tools with all git off-loop via `run_git`.
- *Reliability:* annotations persist; derive-on-read keeps the view from contradicting git; failed
  git ops leave the repo unchanged; typed errors.
- *Portability:* Linux-first with documented degraded lock-identity guard on non-Linux.
- *Simplicity/Footprint:* minimal dependency posture (`mcp` + `aiosqlite`, Starlette/uvicorn
  transitive).
- *Observability:* stdlib logging to stderr, level via `DEV_HELPER_LOG`.

### Implementation Readiness Validation ✅

**Decision Completeness:**
All critical decisions are documented and versioned in Decisions at a Glance; the 11 Invariants
give binding, enforceable rules.

**Structure Completeness:**
A complete `src/` + `tests/` tree is specified, with component boundaries, data boundaries, and the
three primary data-flow paths (mutation tool call, dashboard poll, background refresh) enumerated.

**Pattern Completeness:**
Naming, result-envelope/error taxonomy, async discipline, derive-on-read purity, and logging are
all pinned, each with good/anti-pattern examples that reviewers can reject against.

### Gap Analysis Results

- **Critical Gaps:** none. No missing decision blocks implementation.
- **Important Gaps:** none.
- **Minor Gaps `[RESOLVED 2026-06-22 by amendment]`:** the earlier "~7 vs 6 tools" wording nit is
  moot — the amended surface is **exactly 5** task-centric tools (`create_task`, `list_worktrees`,
  `remove_worktree`, `update_task`, `list_tasks`); `stop`/`--release-lock` remains a CLI command, not
  an MCP tool.

### Validation Issues Addressed

- **FR-12 mechanism (reconciliation vs derive-on-read):** CLOSED via the PRD-Fidelity Gate — intent
  preserved and strengthened; a changelog note is staged for the PRD as a pure traceability edit.
- **Tool-count wording (~7 vs 6):** recorded as a minor traceability nit in Gap Analysis; not a
  blocker for implementation.

### Architecture Completeness Checklist

**Requirements Analysis**

- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**Architectural Decisions**

- [x] Critical decisions documented with versions
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Performance considerations addressed

**Implementation Patterns**

- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Process patterns documented

**Project Structure**

- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION (all 16 checklist items `[x]`, no Critical Gaps open)

**Confidence Level:** High — based on full FR/NFR coverage, internally consistent invariants, and
a structure that mechanically enforces the load-bearing boundaries.

**Key Strengths:**
- Derive-on-read designs OUT the highest-risk subsystem (a stateful reconciliation engine).
- Single chokepoints throughout: one `run_git()` + pools, one `Store`/DB opener, one Origin
  middleware, one SDK-isolation seam.
- Contract-level test coverage is already enumerated per module (envelope shape, error codes,
  snake_case keys, 127.0.0.1 bind, Origin matrix, timeout→kill+reap, projection purity).
- Clean, contained v2-migration path.

**Areas for Future Enhancement:**
- SSE server-push (polling in v1).
- Branch-rename orphan safety net (accepted as a detectable orphan in v1).
- `[AMENDED 2026-06-22]` ~~Multi-repo support.~~ **Now in v1** (global server, multi-repo tasks).
  Future here instead: per-repo base-ref overrides on `create_task`, and `add_worktree` to attach a
  repo to an existing task incrementally.
- SQLite migration *runner* (version-check only in v1).
- CI pipeline (enforced pre-commit hook in v1).

### Implementation Handoff

**AI Agent Guidelines:**

- Follow the 11 Invariants and Decisions at a Glance exactly — they are binding.
- Route every git call through `run_git()` and the correct pool; never call `subprocess`/`os.system`
  for git elsewhere.
- Return the `{ok, data, error}` envelope from every tool; raise typed `DevHelperError` in core
  logic and convert at the adapter — never leak a stack trace.
- Keep core logic free of `mcp`/`starlette` imports (the v2-migration seam).
- Use `snake_case` JSON keys and the single `now_iso()` timestamp helper; add no blocking I/O on the
  event loop.

**First Implementation Priority:**
The uv scaffold story —
```bash
uv init --package dev-helper-mcp
cd dev-helper-mcp
uv add "mcp>=1.28,<2"
uv add "aiosqlite"
```
then build the SDK adapter/`server_factory` (mount `/mcp`, lifespan propagation, Origin middleware)
with its Origin/transport tests, following the implementation sequence in Decision Impact Analysis.
