---
title: dev-helper-mcp
status: final
created: 2026-06-19
updated: 2026-06-19
---

# PRD: dev-helper-mcp
*Working title — confirm.*

> **AMENDMENT 2026-06-22 (multi-repo / global server).** v1 scope changed from *one
> server per repo* to a **single global server per machine**, and the **Task** is now
> the central entity that may span **multiple repositories** — the server creates one
> worktree + `agent/<task>` branch **per repository** for a task. This reverses the
> earlier "one repo per server" / "no multi-repo" positions (§2.2, §5, §3 Glossary,
> FR-13) and resolves Open Question 5. Resolved design choices: repos are supplied
> **implicitly per task** (the agent passes absolute repo path(s); no pre-registration);
> worktrees are created **task-centrically in one call** (`create_task(description,
> repos=[…])`); **status is per task** across all its repos. Affected sections are
> updated in place and tagged `[AMENDED 2026-06-22]`; the companion `addendum.md` and
> `architecture.md` carry the technical impact.

## 0. Document Purpose

This PRD is for the builder (Dev) and any future contributor or downstream BMad workflow
(UX, architecture, epics) picking up `dev-helper-mcp`. It defines **capabilities, not
implementation** — technology choices (Python, MCP SDK, server topology, persistence) live in the
companion `addendum.md`. Vocabulary is anchored in the §3 Glossary and used verbatim throughout.
Features group their Functional Requirements (`FR-N`) under them, and inferred decisions are
tagged inline as `[ASSUMPTION]` and indexed in §9. Scope is calibrated to an **internal tool**.

## 1. Vision

A developer using Claude Code increasingly wants to run **several agents at once** on the same
project — one refactoring tests, another writing docs, another fixing a bug — without those agents
tripping over each other in a single checkout. Git worktrees are the right isolation primitive, but
managing them by hand (creating branches, remembering which worktree maps to which task, cleaning
up afterward) is fiddly, and there is no single place to see *what is running right now*.


`dev-helper-mcp` is a lightweight, locally-run MCP (Model Context Protocol) server that turns
worktree and task management into **tools the agent itself can call**. `[AMENDED 2026-06-22]` It is
a **single global server on the developer's machine** (not one process per repo). An agent registers
a **task** — naming the **one or more repositories** that task touches — and the server spins up an
isolated worktree + branch **per repository** for it, then the agent updates the task's status as it
goes — all through MCP. A small **read-only web dashboard**, served by the same local process, shows
the developer the live picture: every active task, the per-repository worktrees it owns, and the
task's status.

The product deliberately occupies a gap. The 2026 landscape is full of TUI and desktop apps
(Claude Squad, Conductor, Crystal/Nimbalyst, Vibe Kanban) that a *human* drives, and Claude Code now
ships native `--worktree` creation. Almost nothing exposes worktree **and** task management as
MCP tools an agent self-services, paired with a live monitoring view. `dev-helper-mcp` is not a
replacement for native worktrees — it is the **control plane and dashboard** that native worktrees
lack: agent self-service plus a single glance at everything in flight.

## 2. Target User

The user is a **single developer-operator** working on one machine, using Claude Code to run a
small number (typically 2–4) of agents in parallel on one project. They are comfortable with git
and the terminal; they launch the agents themselves and want the tool to handle the worktree
bookkeeping and give them visibility — not take the wheel.

### 2.1 Jobs To Be Done

- *When I split a piece of work across several agents,* I want each agent in its own isolated
  worktree/branch *so they don't corrupt each other's state or collide on a shared checkout.*
- *When I have several agents running,* I want one glance to tell me which worktrees exist and what
  each agent is doing right now *so I don't lose track of work in flight.*
- *When an agent starts a task,* I want it to register and update its own status *so I don't have to
  maintain a manual list.*
- *When work is done,* I want to remove a worktree cleanly *so my repo doesn't accumulate cruft.*
- *As the builder,* I want this to be a small, fast, local tool I can install once and trust to stay
  out of the way.

### 2.2 Non-Users (v1)

- Teams wanting a **shared/multi-user** dashboard or remote access — v1 is single-user, localhost.
- Users who want the tool to **launch or supervise the agents** themselves (see §5).
- `[AMENDED 2026-06-22]` ~~Users working across multiple repositories at once in a single view.~~
  **Now a supported user** — a single global server tracks every repo any active task touches and
  shows them in one dashboard (see §4, §5).

### 2.3 Key User Journeys

*Downscaled per internal single-operator scope. One representative journey.*

- **UJ-1. Dev fans out three agents on one feature and watches them from the dashboard.**
  - **Persona + context:** Dev, on a Linux laptop, has a feature that cleanly splits into three
    independent tasks (API, frontend, tests) and wants to run an agent on each in parallel.
  - **Entry state:** `[AMENDED 2026-06-22]` Dev has started the single global `dev-helper-mcp`
    server once and left it running; the dashboard is open in a browser tab; Claude Code is
    configured to connect to the already-running server over its local endpoint. (The same server
    serves every repo Dev works in.)
  - **Path:** (1) Dev tells each of three Claude Code agents to start, and each agent calls the MCP
    to **create a task** — supplying its description and the **repo path(s)** it touches — which
    creates one worktree + branch per repo with status `running`. (2) As agents work, they **update
    the task's status** (e.g. one hits a blocker → `blocked`). (3) Dev keeps the dashboard tab open
    and sees three tasks, each with its per-repo worktree(s) — branch, path — and live status.
    Realizes UJ-1. *(A task here may span several repos, e.g. a backend + a frontend checkout for
    one feature; the dashboard groups the worktrees under their task.)*
  - **Climax:** At a glance Dev can see all three agents are isolated, what each is doing, and which
    one is blocked — without juggling terminals.
  - **Resolution:** When an agent reports `done`, Dev reviews/merges in their normal flow and asks
    the tool to **remove that worktree**; the dashboard updates to show two remaining.
  - **Edge case:** If an agent tries to create a worktree on a branch already checked out in another
    worktree, the tool refuses with a clear error (git constraint) and the agent picks a new branch.

## 3. Glossary

*Downstream workflows and readers must use these terms exactly.*

- **Repository (repo)** — `[AMENDED 2026-06-22]` any git project on the machine that a task touches,
  identified by its absolute path. The global server tracks **every repo referenced by an active
  task** (no single configured repo, no pre-registration — a repo becomes known when a task names
  it).
- **Worktree** — a git linked working tree (`git worktree`) checked out on its own **branch**,
  giving an agent an isolated copy of *one repo*. `[AMENDED 2026-06-22]` A worktree belongs to
  exactly one **task** in exactly one repo; a task owns at most one worktree **per repo**.
- **Branch** — the git branch a worktree is checked out on (`agent/<task>`). Created together with
  the worktree. The same `agent/<task>` branch name is used in each repo a task spans.
- **Agent** — a Claude Code agent instance the developer launches manually; it is the **caller** of
  the MCP tools. `dev-helper-mcp` does not start or stop agents.
- **Task** — `[AMENDED 2026-06-22]` the central unit of work an agent registers: a description, the
  **set of repositories** it touches (one worktree + branch per repo), a single **status**, and
  timestamps. Created and updated by the agent via MCP tools.
- **Status** — `[AMENDED 2026-06-22b]` the lifecycle state of a task: `running`, `blocked`, `review`,
  `done` (see FR-6 for the canonical four-state set; `blocked` = awaiting input, `review` = awaiting
  the operator's review). One status **per task**, shared across all its repos.
- **MCP tool** — a discrete capability the server exposes to the agent over the Model Context
  Protocol (e.g. create a task with its worktrees, update a task).
- **Dashboard** — the read-only local web view, served by the same process, showing every active
  task, the per-repository worktrees it owns, and its status.
- **Server** — `[AMENDED 2026-06-22]` the single, **long-lived, global** local process — one per
  machine — that the developer starts separately (not spawned per agent session, not one per repo)
  and that hosts both the MCP tool endpoint and the dashboard across all repos. Agents connect to
  it; it outlives any individual agent session they run.

## 4. Features

### 4.1 Worktree Management

**Description:** `[AMENDED 2026-06-22]` The server exposes MCP tools to create a task with its
per-repository worktrees, list worktrees across all tracked repos, and remove a worktree when its
work is finished. Worktree creation is **task-centric**: in one call the agent names the task and the
**1+ repositories** it touches, and the server creates one worktree + `agent/<task>` branch **per
repo** off each repo's base ref. Operations are thin, safe wrappers over `git worktree` with
guardrails so an agent cannot leave any repo in a broken state. Realizes UJ-1. Merge-back is **out of
scope** for v1 (§5) — the developer merges in their normal flow.

**Functional Requirements:**

#### FR-1: Create a task with one worktree+branch per repository `[AMENDED 2026-06-22]`

An agent can create a task in a single call, supplying a description, the **set of repository paths**
the task touches, and a task name; for **each repository** the server creates a new worktree on a
newly created branch `agent/<task>` from that repo's base ref (default: the repo's current HEAD). Each
worktree is created in a sibling directory `<repo-parent>/<repo>.worktrees/<task>/` (outside the repo
tree, so no nested-worktree recursion or IDE/file-watcher confusion), where `<task>` is derived from
the caller-supplied task name. Realizes UJ-1. *(Task registration semantics — status, persistence,
one-active-per-task — are in FR-4; this FR covers the worktree side-effect across repos.)*

**Consequences (testable):**
- For each requested repo a new worktree directory and a new `agent/<task>` branch are created and
  tracked; the tool returns the `task_id` and a per-repo list of `{repo_path, worktree_path, branch}`.
- Each supplied path is validated to be a git repository before any worktree is created; a
  non-repo path returns a structured error and nothing is created in any repo.
- If `agent/<task>` already exists or is checked out in **any** requested repo, the tool refuses with
  a clear, structured error and **makes no change in any repo** (the create is all-or-nothing across
  the repo set; git's same-branch constraint applies per repo).
- The task name is normalized to the `<task>` slug by documented rules (addendum §6). On a slug
  collision (the branch or target directory already exists in any requested repo), the tool
  **rejects with a structured "name already in use" error** rather than silently suffixing — keeping
  agent behavior predictable.
- A pre-existing target directory that is not a tracked worktree (e.g. a stale leftover) in any repo
  is detected and reported as a structured error, not passed through as a raw git failure.
- If a base ref does not exist in a requested repo, the tool returns an error and creates nothing in
  any repo.
- **Rollback (error-safe) `[decided 2026-06-22]`:** if worktree creation fails in a later repo after
  earlier repos succeeded, the tool compensates — removes the already-created worktrees and deletes
  their `agent/<task>` branches — and persists no task record. If a compensating teardown itself
  fails, the tool returns a structured `RollbackIncomplete` error naming the repo paths left orphaned
  (never silent). Full crash-safety (surviving a process kill mid-create) is a **documented v1
  non-goal**: any residue is a no-DB-row orphan worktree, surfaced on the dashboard as untracked and
  recoverable on retry — not silent corruption.

#### FR-2: List worktrees `[AMENDED 2026-06-22]`

An agent or the dashboard can list all worktrees currently tracked across **all repos any active
task touches**, each with its repo, path, branch, and associated task (if any). Filterable by repo
and/or task. Realizes UJ-1.

**Consequences (testable):**
- The list reflects the actual on-disk git worktrees for each tracked repo (derived from git, not
  just an internal cache — see FR-12).
- Each entry includes repo path, worktree path, branch, and linked task id/status when a task exists.

#### FR-3: Remove worktree `[AMENDED 2026-06-22]`

An agent or the developer can remove one tracked worktree (identified by task + repo, or by path),
with an option to also delete its branch. Removal is per-worktree; a task's remaining worktrees in
other repos are unaffected.

**Consequences (testable):**
- Removing a worktree deletes that repo's working tree and de-tracks it; the task's link to that
  repo is dropped. When a task has no worktrees left, its task record is closed/detached (see FR-6).
- The tool refuses to remove a worktree with uncommitted changes unless an explicit `force` flag is
  passed, and says why. "Uncommitted changes" is defined precisely (tracked-dirty + staged; untracked
  handling stated) in the addendum.
- Branch deletion happens only when explicitly requested **and** is guarded separately from worktree
  removal: deleting a branch with unmerged commits requires its own distinct, explicit flag, and the
  tool surfaces "branch has N unmerged commits" first. A single `force` flag never silently destroys
  unmerged work — which matters because merge-back is out of scope, so branches accumulate it.

**Out of Scope:** Merging or rebasing the branch back to the mainline; that is the developer's flow.

### 4.2 Per-Agent Task Tracking

**Description:** `[AMENDED 2026-06-22]` Agents self-report their work. When an agent begins a task it
registers it through the `create_task` tool (FR-1) — a description, the **set of repositories** it
touches, and an initial status — and updates the status as work progresses. The task model is
intentionally small. A task owns one worktree per repo so the dashboard can show "this task, these
per-repo worktrees, this status." Realizes UJ-1.

**Functional Requirements:**

#### FR-4: Register task `[AMENDED 2026-06-22]`

An agent creates a task (via `create_task`, FR-1) with a description and its set of repositories,
defaulting status to `running`. Realizes UJ-1.

**Consequences (testable):**
- A task is persisted with a stable `task_id`, description, status, created/updated timestamps, and
  its set of `(repo_path, branch, worktree_path)` worktree links.
- The task name (`<task>` slug) is the active-task key: at most one active (non-`done`) task may use
  a given `<task>` slug; this is enforced **atomically** at the persistence layer (not a
  check-then-write), so two near-simultaneous `create_task` calls with the same name cannot both
  succeed — the second returns a conflict error. `[ASSUMPTION: A2]`
- Once a task is `done`, its `<task>` slug may be reused for a new task (sequential reuse is
  allowed); the dashboard shows the current active task.

#### FR-5: Update task status and description

An agent can update an existing task's status and/or description.

**Consequences (testable):**
- A status update changes the persisted status and bumps the updated timestamp; the dashboard
  reflects it on its next refresh.
- Updating a non-existent task returns a clear not-found error.

#### FR-6: Canonical task status set `[AMENDED 2026-06-22b]`

The task status is one of a fixed, documented set of **four** states: `running`, `blocked`, `review`,
`done`, where:
- `running` — the agent is actively working.
- `blocked` — the agent is awaiting the operator's **input** (stuck mid-work; cannot proceed).
- `review` — the agent has **finished**; work awaits the operator's **review** (the operator reviews
  in their own flow — the tool does not merge). An *active*, non-`done` state.
- `done` — reviewed/closed (terminal).

**Consequences (testable):**
- Any status outside the four-state set is rejected.
- `review` counts as an **active** (non-`done`) task for the one-active-task-per-slug rule (FR-4);
  only `done` is closed.
- A `done` task is treated as closed: it no longer counts as an active task for its `<task>` slug
  and is visually distinguished on the dashboard (dimmed).

#### FR-7: List tasks `[AMENDED 2026-06-22]`

An agent or the dashboard can list tasks, filterable by status and/or repository, each task
including its per-repo worktree links.

**Consequences (testable):**
- The returned tasks match the filter and include all model fields, including the list of
  `(repo_path, branch, worktree_path)` links.

### 4.3 Live Web Dashboard

**Description:** `[AMENDED 2026-06-22]` A **read-only** web page, served by the same global process,
gives the developer the at-a-glance picture: every active task, the per-repository worktrees it owns,
and its status — across all repos any active task touches. It is for **monitoring, not control** — no
worktrees are created, no tasks edited, and no agents launched from the UI (§5). The view refreshes
automatically so the developer can leave it open in a tab. Realizes UJ-1.

**Functional Requirements:**

#### FR-8: Live monitoring view `[AMENDED 2026-06-22]`

The dashboard displays a board of tasks; each task shows its description, status, and its per-repo
worktrees (repo, branch, path). `[AMENDED 2026-06-22b]` The board is organized as **three active
status columns** — Running | Blocked | Review — with `done` kept as a foldable `✓ N done` count
below the board (not a column, since done needs no action and accretes). Blocked is the visually
emphasized "alarm" state. States are distinct by position, color, left bar, and per-card glyph (see
the UX spec at `ux-designs/ux-dev-helper-mcp-2026-06-22/`). Worktrees are grouped under their task.
Realizes UJ-1.

**Consequences (testable):**
- Every active task and its per-repo worktrees and status appear; each worktree's repo is identified.
- The view reflects task and worktree state created via the MCP tools across all tracked repos.

#### FR-9: Automatic refresh

The dashboard updates to reflect current state without a manual reload. `[ASSUMPTION: A1]` v1
baseline is short-interval polling of a state endpoint; server-push (SSE) is a vNext enhancement
(both depend on the long-lived server of FR-13).

**Consequences (testable):**
- After an agent updates a task status, the open dashboard reflects the change within **≤ 3 seconds**
  with no user action `[decided 2026-06-22]` (for ≤ 15 tracked repos; see NFR-Performance).

#### FR-10: Read-only guarantee

The dashboard exposes no mutating action.

**Consequences (testable):**
- The dashboard's served interface offers no control to create/modify/remove worktrees or tasks, or
  to launch agents.

### 4.4 MCP Server & Tool Surface

**Description:** `dev-helper-mcp` is an MCP server exposing a **small, well-named** set of tools
covering the worktree and task capabilities above. The surface is deliberately tight so it stays
well under client tool-count limits and is easy for an agent to use correctly. All tool calls are
**fast and synchronous** — there are no long-blocking operations behind a tool call.

**Functional Requirements:**

#### FR-11: Discoverable, documented tool surface `[AMENDED 2026-06-22]`

The server advertises its tools over MCP with clear names and descriptions covering task creation
with per-repo worktrees (`create_task`), worktree list/remove, and task update/list.

**Consequences (testable):**
- A connected MCP client (Claude Code) can enumerate the tools and their input/output schemas.
- The total exposed tool count stays small (target well under client caps — see NFR). The
  task-centric `create_task` keeps the surface tight even though tasks now span repos.

**Feature-specific NFRs:**
- Tool calls return quickly enough to never approach MCP transport timeouts (see Cross-Cutting NFRs).

### 4.5 State Persistence & Server Lifecycle

**Description:** `[AMENDED 2026-06-22]` The server keeps task records and their per-repo worktree
links in local persistent state (in a **machine-global** location, not per-repo) so the picture
survives a restart, and derives the live view against the actual git worktrees of each tracked repo
so the dashboard never lies. The server is started once by the developer, serves all repos, and binds
to localhost only.

**Functional Requirements:**

#### FR-12: State persistence and git-derived view `[AMENDED 2026-06-22]`

Task records and their per-repo worktree links persist across server restarts in a machine-global
store, and the live view is derived against **each tracked repo's** actual git worktrees, with
`git worktree list` (porcelain) **per repo** as the single source of truth for worktree existence
(not directory scanning). `[ASSUMPTION: A2]`

**Consequences (testable):**
- After a restart, previously registered tasks and their per-repo worktree links are still present.
- A worktree deleted out-of-band in any repo (e.g. via raw `git worktree remove`) is detected and no
  longer shown as active; the task's link to that repo is marked detached/closed.
- A worktree created out-of-band (no task) in a tracked repo appears in the listing with no
  associated task.
- The git-derived view is **not** recomputed per dashboard refresh: it is computed on tool calls and
  on a periodic background tick (fanning out across tracked repos), and the dashboard is served from
  the cached derived state (so auto-refresh does not cause a git shell-out per repo on every poll).

#### FR-13: Global server lifecycle `[AMENDED 2026-06-22]`

The developer **starts one server separately** and leaves it running; it is a **long-lived, global**
process (one per machine, not per repo, not spawned per agent session) that serves both the MCP
endpoint and the dashboard for all repos, bound to `127.0.0.1`. Agents connect to the already-running
server. Only **one instance per machine** runs at a time. `[ASSUMPTION: A3, A5]`

**Consequences (testable):**
- Starting the server makes both the MCP tools and the dashboard available, and the server prints
  the dashboard URL on startup.
- The server outlives any individual agent/Claude Code session (the dashboard and state persist while
  agents come and go) and serves tasks across any repo without restart.
- Starting a second instance on the same machine is detected and refused (or attaches to the existing
  one) rather than failing with an opaque port-in-use error.
- The server is not reachable from another host (localhost bind).

## 5. Non-Goals (Explicit)

- **The tool does not launch, stop, or supervise agents.** The developer starts Claude Code agents
  manually; `dev-helper-mcp` only manages worktrees and tracks self-reported tasks. *(D3)*
- **No merge-back / rebase / PR automation.** The developer integrates branches in their normal
  flow. *(D7)*
- `[AMENDED 2026-06-22]` ~~No multi-repository view. One repo per running server in v1. (D8)~~
  **Reversed:** a single global server tracks every repo any active task touches and shows them in
  one dashboard; a task may span multiple repos (one worktree per repo). Still **single-user,
  localhost** (the multi-*user* non-goal below stands).
- **No write actions from the dashboard.** Monitoring only. *(D4)*
- **No remote/multi-user access or auth.** Single user, localhost. *(A3)*
- **Not a replacement for Claude Code's native `--worktree`.** It is the task-tracking + dashboard +
  agent-self-service layer on top. *(R2)*
- `[NON-GOAL for MVP]` **No same-file collision pre-warning** across agents and **no worktree
  bootstrap automation** (copying `.env`, assigning ports, running installs) — both are known
  industry gaps named in §8, deferred deliberately.

## 6. MVP Scope

### 6.1 In Scope

- `[AMENDED 2026-06-22]` MCP tools: create a task with one worktree+branch per repo, list worktrees
  (across repos), remove worktree (FR-1–3).
- MCP tools: register task (via create_task), update task, list tasks, fixed status set (FR-4–7).
- `[AMENDED 2026-06-22]` Read-only auto-refreshing web dashboard of tasks × per-repo worktrees,
  across all tracked repos (FR-8–10).
- Small, discoverable MCP tool surface (FR-11).
- `[AMENDED 2026-06-22]` Local persistence (machine-global) with per-repo git-derived view; single
  global localhost server (FR-12–13).
- **Multi-repo tasks:** a task may span 1+ repos, one worktree each, supplied implicitly per task.
- Python implementation (see `addendum.md`).

### 6.2 Out of Scope for MVP

- Agent launching/supervision — reason: explicit scope decision (D3).
- Merge-back / PR automation — reason: developer's existing flow (D7).
- `[AMENDED 2026-06-22]` ~~Multi-repo dashboard — out of scope for v1 (D8).~~ **Now IN scope** —
  the single-operator habit was confirmed to span repos, so the global server + multi-repo tasks are
  v1 (see §4, Amendment note). What remains out of scope: per-repo *base-ref-per-repo* overrides on
  create (each repo defaults to its own HEAD) and adding a repo to an existing task incrementally —
  both deferred.
- Dashboard write actions — reason: monitoring-only decision (D4).
- Same-file collision warnings; worktree bootstrap (`.env`/deps/ports) — reason: hard, industry-wide
  unsolved problems; deferred to keep v1 small (§8). `[NOTE FOR PM: worktree bootstrap is the most
  emotionally load-bearing deferral — fresh worktrees with no deps are a real friction point.]`
- Auth / remote access — reason: single-user local tool (A3).

## 7. Success Metrics

*Internal-tool scope — qualitative primaries with a couple of measurable proxies.*

**Primary**
- **SM-1**: *Self-adoption.* Dev runs 2–4 parallel agents through the tool on real work weekly and
  doesn't abandon it after a month. Validates the product as a whole (FR-1–13).
- **SM-2**: *Glanceability.* From the dashboard, Dev can correctly state what every running agent is
  doing in under ~10 seconds, without opening a terminal. Validates FR-8, FR-9.

**Secondary**
- **SM-3**: *Spin-up friction.* Creating an isolated worktree+branch for a new agent task is a single
  MCP tool call with no manual git steps. Validates FR-1.
- **SM-4**: *No stale lies.* The dashboard never shows a worktree/task state that contradicts the
  actual git state (after reconciliation). Validates FR-12.

**Counter-metrics (do not optimize)**
- **SM-C1**: *Tool-call latency.* Do not let feature growth push MCP tool-call latency up — calls
  must stay snappy and well clear of transport timeouts. Counterbalances SM-1/SM-3. *(R3)*
- **SM-C2**: *Tool-surface size.* Do not grow the MCP tool count chasing features; a bloated surface
  hurts agent usability and risks client caps. Counterbalances feature additions. *(R4)*

## 8. Open Questions

1. ~~**Worktree naming convention (A4).**~~ **RESOLVED 2026-06-19:** worktrees live in a sibling
   directory `<repo-parent>/<repo>.worktrees/<task>/`; branches are named `agent/<task>`.
2. **Merge-back assistance.** Deferred for v1 — is even a read-only "branch is N commits ahead /
   behind, here's the diff link" view wanted in vNext? *Owner: Dev. Revisit: post-v1.*
3. **Same-file collision pre-warning.** Industry-wide unsolved; would a "which worktrees touch which
   paths" view be a future differentiator? *Owner: Dev. Revisit: post-v1.*
4. **Worktree bootstrap automation.** Auto-copy `.env`, assign unique ports, run installs in fresh
   worktrees — cited as a top friction point. Future scope? *Owner: Dev. Revisit: post-v1.*
5. ~~**Multi-repo.** When (if) does single-operator usage actually span repos?~~ **RESOLVED
   2026-06-22:** yes — usage spans repos in practice. v1 is a **single global server** with
   **multi-repo tasks** (one worktree per repo, repos supplied implicitly per task). See the
   Amendment note and §4.

## 9. Assumptions Index

*Every `[ASSUMPTION]` surfaced for confirmation (cross-referenced to the decision log):*

- **A1** (§4.3 FR-9) — Dashboard + MCP server run as one local process; the dashboard auto-refreshes
  (polling or SSE).
- **A2** (§4.2 FR-4, §4.5 FR-12) — Task/worktree state is persisted to local storage (file or small
  SQLite DB) in a known per-project location.
- **A3** (§4.5 FR-13, §5) — Server binds `127.0.0.1` only; no auth in v1.
- `[AMENDED 2026-06-22]` **A2** now also covers: state is **machine-global** (not per-repo); the
  store keys tasks by `<task>` slug and links each to its per-repo `(repo_path, branch, worktree)`.
- **A4** (§4.1 FR-1) — **RESOLVED 2026-06-19:** worktrees in sibling `<repo-parent>/<repo>.worktrees/<task>/`;
  branches `agent/<task>`. No longer an assumption.
- **A5** (§4.5 FR-13) — `[AMENDED 2026-06-22]` ~~"Single repo at a time."~~ Now: **one global
  server per machine**; "single instance" = one process per machine (port-bind + global lockfile),
  serving every repo a task references.

---

## Cross-Cutting NFRs

System-wide non-functional requirements not tied to a single feature.

- **NFR-Performance.** Task/state tool calls return sub-second. Worktree creation is bounded by
  `git worktree add` checking out a working tree — sub-second on a typical repo, though it may take
  several seconds on a large repo or cold cache. The target is therefore scoped to "typical repo," and
  the operation must never approach MCP transport timeouts (~5 min). Dashboard state changes are
  visible within **≤ 3 seconds** `[decided 2026-06-22]` **for ≤ 15 tracked repos** (the SLO is
  explicitly bounded by repo count, since the live view fans `git worktree list` across every tracked
  repo; a slow repo degrades that repo only, never the whole dashboard). *(R3)*
- **NFR-MCP-Compatibility.** Tool calls are *protocol*-synchronous and short-lived (returning well
  before the transport timeout), but this must **not** be conflated with blocking the runtime: no git
  shell-out or other blocking call runs on the server's event loop. Such work runs off-loop (e.g. an
  async subprocess or a worker thread), so the dashboard and concurrent tool calls stay responsive. The
  exposed tool count stays small and well under client caps (~40). *(R3, R4)*
- **NFR-Reliability.** State survives a server restart; the server reconciles tracked state against
  actual git worktrees so the dashboard never shows a contradicted state (FR-12). Tool errors are
  structured and actionable; failed git operations leave the repo unchanged.
- **NFR-Security/Locality.** Localhost-only bind; no remote exposure; no secrets handled. Single-user
  trust model for v1. Because the dashboard is a browser page and (per the addendum) the MCP endpoint
  is local HTTP, both the MCP endpoint and the dashboard routes **must validate the `Origin` header**
  and reject non-localhost origins. This prevents DNS-rebinding attacks that drive the local server
  from a malicious web page, and the MCP HTTP transport spec mandates it. *(A3)*
- **NFR-Portability.** Linux is the primary target (developer's environment); avoid hard
  platform-specific assumptions so macOS works too.
- **NFR-Simplicity/Footprint.** Single, easy local install with minimal dependencies; the tool stays
  out of the way and is cheap to run alongside several agents.
- **NFR-Observability.** Local logs sufficient to diagnose a failed tool call or a reconciliation
  mismatch.

## Constraints & Guardrails

- **Safety.** Destructive git operations (worktree/branch removal) require explicit intent and refuse
  on uncommitted changes without `force` (FR-3). The tool never force-pushes or rewrites mainline.
- **Privacy.** No telemetry leaves the machine; no external network calls required for core function.
- **Cost.** Effectively zero — a local process; the real parallelism limit is the developer's Claude
  Code provider rate limits, not this tool (noted from research as the actual cap on parallel
  agents).

## Developer-Product Surface

*This is a tool an agent integrates against; the MCP tool surface is a contract.*

- **MCP tool contract.** The set of tool names, their purpose, and input/output shapes is the public
  surface Claude Code depends on. Capability-level definitions are in §4; concrete schemas live in
  `addendum.md`.
- **Versioning / stability.** Tool names and their input/output contracts should stay stable once an
  agent relies on them; breaking changes to a tool's shape are a versioning event. *(detail →
  addendum)*
- **Language / runtime target.** Python with the official MCP SDK; runs locally on the developer's
  machine (Linux-first). *(detail → addendum)*
