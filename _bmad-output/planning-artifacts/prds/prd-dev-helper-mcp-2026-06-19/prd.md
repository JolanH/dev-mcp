---
title: dev-helper-mcp
status: final
created: 2026-06-19
updated: 2026-06-19
---

# PRD: dev-helper-mcp
*Working title — confirm.*

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
worktree and task management into **tools the agent itself can call**. An agent can spin up an
isolated worktree for its task, register what it is working on, and update its status as it goes —
all through MCP. A small **read-only web dashboard**, served by the same local process, shows the
developer the live picture: the active repository, its worktrees, and the per-agent task and status
for each.

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
- Users working across **multiple repositories at once** in a single view (see §5).

### 2.3 Key User Journeys

*Downscaled per internal single-operator scope. One representative journey.*

- **UJ-1. Dev fans out three agents on one feature and watches them from the dashboard.**
  - **Persona + context:** Dev, on a Linux laptop, has a feature that cleanly splits into three
    independent tasks (API, frontend, tests) and wants to run an agent on each in parallel.
  - **Entry state:** Dev has started `dev-helper-mcp` separately, pointed at the project root, and
    left it running; the dashboard is open in a browser tab; Claude Code is configured to connect to
    the already-running server over its local endpoint.
  - **Path:** (1) Dev tells each of three Claude Code agents to start, and each agent calls the MCP
    to **create a worktree + branch** for its task and to **register its task** (description +
    status `running`). (2) As agents work, they **update status** (e.g. one hits a blocker →
    `blocked`). (3) Dev keeps the dashboard tab open and sees three worktrees, each with its branch,
    path, task description, and live status. Realizes UJ-1.
  - **Climax:** At a glance Dev can see all three agents are isolated, what each is doing, and which
    one is blocked — without juggling terminals.
  - **Resolution:** When an agent reports `done`, Dev reviews/merges in their normal flow and asks
    the tool to **remove that worktree**; the dashboard updates to show two remaining.
  - **Edge case:** If an agent tries to create a worktree on a branch already checked out in another
    worktree, the tool refuses with a clear error (git constraint) and the agent picks a new branch.

## 3. Glossary

*Downstream workflows and readers must use these terms exactly.*

- **Repository (repo)** — the single git project `dev-helper-mcp` is pointed at for a run. One repo
  per running server instance in v1.
- **Worktree** — a git linked working tree (`git worktree`) checked out on its own **branch**,
  giving an agent an isolated copy of the repo. One worktree maps to at most one active **task**.
- **Branch** — the git branch a worktree is checked out on. Created together with the worktree.
- **Agent** — a Claude Code agent instance the developer launches manually; it is the **caller** of
  the MCP tools. `dev-helper-mcp` does not start or stop agents.
- **Task** — a self-reported unit of work an agent registers: a description, the worktree/branch it
  targets, a **status**, and timestamps. Created and updated by the agent via MCP tools.
- **Status** — the lifecycle state of a task: `running`, `blocked`, `done` (see FR-6 for the
  canonical set).
- **MCP tool** — a discrete capability the server exposes to the agent over the Model Context
  Protocol (e.g. create a worktree, register a task).
- **Dashboard** — the read-only local web view, served by the same process, showing the repo, its
  worktrees, and each worktree's task and status.
- **Server** — the single, **long-lived** local process that the developer starts separately (not
  spawned per agent session) and that hosts both the MCP tool endpoint and the dashboard for one
  repo. Agents connect to it; it outlives any individual agent session they run.

## 4. Features

### 4.1 Worktree Management

**Description:** The server exposes MCP tools for the create / list / remove lifecycle of git
worktrees within the single configured repo. An agent (or the developer) creates a worktree together
with a new branch off a base ref, lists the worktrees currently tracked, and removes one when its
work is finished. Operations are thin, safe wrappers over `git worktree` with guardrails so an agent
cannot leave the repo in a broken state. Realizes UJ-1. Merge-back is **out of scope** for v1
(§5) — the developer merges in their normal flow.

**Functional Requirements:**

#### FR-1: Create worktree with branch

An agent can create a new worktree on a newly created branch from a specified base ref (default: the
repo's current HEAD). The worktree is created in a sibling directory `<repo-parent>/<repo>.worktrees/<task>/`
(outside the repo tree, so no nested-worktree recursion or IDE/file-watcher confusion), and the
branch is named `agent/<task>`, where `<task>` is derived from a caller-supplied task name. Realizes
UJ-1.

**Consequences (testable):**
- A new worktree directory and a new branch are created and tracked; the tool returns the worktree
  path and branch name.
- If the requested branch already exists or is checked out in another worktree, the tool refuses
  with a clear, structured error and makes no change (git's same-branch constraint).
- The task name is normalized to the `<task>` slug by documented rules (addendum §6). On a slug
  collision (the branch or target directory already exists), the tool **rejects with a structured
  "name already in use" error** rather than silently suffixing — keeping agent behavior predictable.
- A pre-existing target directory that is not a tracked worktree (e.g. a stale leftover) is detected
  and reported as a structured error, not passed through as a raw git failure.
- If the base ref does not exist, the tool returns an error and creates nothing.

#### FR-2: List worktrees

An agent or the dashboard can list all worktrees currently tracked for the repo, each with its
path, branch, and associated task (if any). Realizes UJ-1.

**Consequences (testable):**
- The list reflects the actual on-disk git worktrees for the repo (reconciled, not just an internal
  cache — see FR-12).
- Each entry includes path, branch, and linked task id/status when a task exists.

#### FR-3: Remove worktree

An agent or the developer can remove a tracked worktree, with an option to also delete its branch.

**Consequences (testable):**
- Removing a worktree deletes the linked working tree and de-tracks it; its task is marked closed or
  detached (see FR-6).
- The tool refuses to remove a worktree with uncommitted changes unless an explicit `force` flag is
  passed, and says why. "Uncommitted changes" is defined precisely (tracked-dirty + staged; untracked
  handling stated) in the addendum.
- Branch deletion happens only when explicitly requested **and** is guarded separately from worktree
  removal: deleting a branch with unmerged commits requires its own distinct, explicit flag, and the
  tool surfaces "branch has N unmerged commits" first. A single `force` flag never silently destroys
  unmerged work — which matters because merge-back is out of scope, so branches accumulate it.

**Out of Scope:** Merging or rebasing the branch back to the mainline; that is the developer's flow.

### 4.2 Per-Agent Task Tracking

**Description:** Agents self-report their work. When an agent begins a task it registers it through
an MCP tool — a description, the worktree/branch it targets, and an initial status — and updates the
status as work progresses. The task model is intentionally small. Tasks are tied to a worktree so
the dashboard can show "this worktree, this agent, this work, this status." Realizes UJ-1.

**Functional Requirements:**

#### FR-4: Register task

An agent can create a task with a description and a target worktree/branch, defaulting status to
`running`. Realizes UJ-1.

**Consequences (testable):**
- A task is persisted with a stable id, description, linked worktree/branch, status, and
  created/updated timestamps.
- A worktree has at most one active (non-`done`) task; this is enforced **atomically** at the
  persistence layer (not a check-then-write), so two near-simultaneous registrations against the
  same worktree cannot both succeed — the second returns a conflict error. `[ASSUMPTION: A2]`
- Once a worktree's task is `done`, a new task may be registered against that worktree (sequential
  reuse is allowed); the dashboard shows the current active task.

#### FR-5: Update task status and description

An agent can update an existing task's status and/or description.

**Consequences (testable):**
- A status update changes the persisted status and bumps the updated timestamp; the dashboard
  reflects it on its next refresh.
- Updating a non-existent task returns a clear not-found error.

#### FR-6: Canonical task status set

The task status is one of a fixed, documented set: `running`, `blocked`, `done`.

**Consequences (testable):**
- Any status outside the set is rejected.
- A `done` task is treated as closed: it no longer counts as the worktree's active task and is
  visually distinguished on the dashboard.

#### FR-7: List tasks

An agent or the dashboard can list tasks, filterable by status and/or worktree.

**Consequences (testable):**
- The returned tasks match the filter and include all model fields.

### 4.3 Live Web Dashboard

**Description:** A **read-only** web page, served by the same local process, gives the developer the
at-a-glance picture: the active repo, every tracked worktree, and each worktree's task and status.
It is for **monitoring, not control** — no worktrees are created, no tasks edited, and no agents
launched from the UI (§5). The view refreshes automatically so the developer can leave it open in a
tab. Realizes UJ-1.

**Functional Requirements:**

#### FR-8: Live monitoring view

The dashboard displays the configured repo and a list/board of its worktrees, each showing branch,
path, linked task description, and status, with `done`/`blocked` states visually distinct. Realizes
UJ-1.

**Consequences (testable):**
- Every tracked worktree and its current task/status appear; the active repo is identified.
- The view reflects task and worktree state created via the MCP tools.

#### FR-9: Automatic refresh

The dashboard updates to reflect current state without a manual reload. `[ASSUMPTION: A1]` v1
baseline is short-interval polling of a state endpoint; server-push (SSE) is a vNext enhancement
(both depend on the long-lived server of FR-13).

**Consequences (testable):**
- After an agent updates a task status, the open dashboard reflects the change within **≤ 3 seconds**
  with no user action.

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

#### FR-11: Discoverable, documented tool surface

The server advertises its tools over MCP with clear names and descriptions covering worktree
create/list/remove and task register/update/list.

**Consequences (testable):**
- A connected MCP client (Claude Code) can enumerate the tools and their input/output schemas.
- The total exposed tool count stays small (target well under client caps — see NFR).

**Feature-specific NFRs:**
- Tool calls return quickly enough to never approach MCP transport timeouts (see Cross-Cutting NFRs).

### 4.5 State Persistence & Server Lifecycle

**Description:** The server keeps worktree-to-task associations and task records in local persistent
state so the picture survives a restart, and reconciles that state against the actual git worktrees
on disk so the dashboard never lies. The server is started by the developer, pointed at one repo,
and binds to localhost only.

**Functional Requirements:**

#### FR-12: State persistence and reconciliation

Task and worktree-association state persists across server restarts and is reconciled against the
repo's actual git worktrees, with `git worktree list` (porcelain) as the single source of truth for
worktree existence (not directory scanning). `[ASSUMPTION: A2]`

**Consequences (testable):**
- After a restart, previously registered tasks and their worktree links are still present.
- A worktree deleted out-of-band (e.g. via raw `git worktree remove`) is detected and no longer
  shown as active; its task is marked detached/closed.
- A worktree created out-of-band (no task) appears in the listing with no associated task.
- Reconciliation is **not** triggered per dashboard refresh: it runs on tool calls and on a periodic
  background tick, and the dashboard is served from the reconciled cached state (so auto-refresh does
  not cause a git shell-out on every poll).

#### FR-13: Local server lifecycle

The developer **starts the server separately** and leaves it running, pointed at a single repo; it
is a **long-lived** process (not spawned per agent session) that serves both the MCP endpoint and the
dashboard, bound to `127.0.0.1`. Agents connect to the already-running server. Only **one instance
per repo** runs at a time. `[ASSUMPTION: A3, A5]`

**Consequences (testable):**
- Starting the server makes both the MCP tools and the dashboard available, and the server prints
  the dashboard URL on startup.
- The server outlives any individual agent/Claude Code session (the dashboard and state persist while
  agents come and go).
- Starting a second instance against the same repo is detected and refused (or attaches to the
  existing one) rather than failing with an opaque port-in-use error.
- The server is not reachable from another host (localhost bind).

## 5. Non-Goals (Explicit)

- **The tool does not launch, stop, or supervise agents.** The developer starts Claude Code agents
  manually; `dev-helper-mcp` only manages worktrees and tracks self-reported tasks. *(D3)*
- **No merge-back / rebase / PR automation.** The developer integrates branches in their normal
  flow. *(D7)*
- **No multi-repository view.** One repo per running server in v1. *(D8)*
- **No write actions from the dashboard.** Monitoring only. *(D4)*
- **No remote/multi-user access or auth.** Single user, localhost. *(A3)*
- **Not a replacement for Claude Code's native `--worktree`.** It is the task-tracking + dashboard +
  agent-self-service layer on top. *(R2)*
- `[NON-GOAL for MVP]` **No same-file collision pre-warning** across agents and **no worktree
  bootstrap automation** (copying `.env`, assigning ports, running installs) — both are known
  industry gaps named in §8, deferred deliberately.

## 6. MVP Scope

### 6.1 In Scope

- MCP tools: create worktree+branch, list worktrees, remove worktree (FR-1–3).
- MCP tools: register task, update task, list tasks, fixed status set (FR-4–7).
- Read-only auto-refreshing web dashboard of repo × worktrees × tasks (FR-8–10).
- Small, discoverable MCP tool surface (FR-11).
- Local persistence with git reconciliation; single-repo, localhost server (FR-12–13).
- Python implementation (see `addendum.md`).

### 6.2 Out of Scope for MVP

- Agent launching/supervision — reason: explicit scope decision (D3).
- Merge-back / PR automation — reason: developer's existing flow (D7).
- Multi-repo dashboard — reason: scope control for v1 (D8). `[NOTE FOR PM: revisit if the
  single-operator habit turns out to span repos in practice.]`
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
5. **Multi-repo.** When (if) does single-operator usage actually span repos, justifying the
   multi-repo dashboard? *Owner: Dev. Revisit: post-v1.*

## 9. Assumptions Index

*Every `[ASSUMPTION]` surfaced for confirmation (cross-referenced to the decision log):*

- **A1** (§4.3 FR-9) — Dashboard + MCP server run as one local process; the dashboard auto-refreshes
  (polling or SSE).
- **A2** (§4.2 FR-4, §4.5 FR-12) — Task/worktree state is persisted to local storage (file or small
  SQLite DB) in a known per-project location.
- **A3** (§4.5 FR-13, §5) — Server binds `127.0.0.1` only; no auth in v1.
- **A4** (§4.1 FR-1) — **RESOLVED 2026-06-19:** worktrees in sibling `<repo-parent>/<repo>.worktrees/<task>/`;
  branches `agent/<task>`. No longer an assumption.
- **A5** (§4.5 FR-13) — "Single repo at a time" = the server is pointed at one project root per run.

---

## Cross-Cutting NFRs

System-wide non-functional requirements not tied to a single feature.

- **NFR-Performance.** Task/state tool calls return sub-second. Worktree creation is bounded by
  `git worktree add` checking out a working tree — sub-second on a typical repo, though it may take
  several seconds on a large repo or cold cache. The target is therefore scoped to "typical repo," and
  the operation must never approach MCP transport timeouts (~5 min). Dashboard state changes are
  visible within **≤ 3 seconds**. *(R3)*
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
