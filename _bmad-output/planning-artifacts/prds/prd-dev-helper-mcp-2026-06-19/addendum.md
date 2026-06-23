# Technical Addendum — dev-helper-mcp

Companion to `prd.md`. Holds the technical-how, mechanism/transport decisions, candidate tool
schemas, and rejected-alternative rationale that the PRD deliberately keeps out of its capability
narrative. This is **input for the architecture workflow**, not a final design — it captures intent
and constraints so `bmad-create-architecture` has a running start. Decisions trace to the
`.decision-log.md` IDs.

> **AMENDMENT 2026-06-22 (multi-repo / global server).** The PRD was amended so v1 is a **single
> global server per machine** and a **task may span multiple repositories** (one worktree +
> `agent/<task>` branch per repo). Repos are supplied **implicitly per task** (absolute path(s),
> no pre-registration); worktrees are created **task-centrically in one call**; **status is per
> task**. Technical impact below is tagged `[AMENDED 2026-06-22]`: topology (§1), transport &
> repo-binding & single-instance (§2), tool schemas (§3 — `create_task`), persistence schema (§4 —
> `task` + `task_worktree` tables, machine-global DB), dashboard (§5 — per-repo fan-out), and
> conventions (§6 — global state location).

## 1. Stack decision (D5)

- **Language/runtime:** Python. Use the official MCP SDK (`mcp` / `modelcontextprotocol` Python
  package). Rationale: user preference; mature official SDK; good `git` and HTTP story.
- **Git access:** shell out to the `git` CLI (`git worktree add/list/remove`, `git branch`) rather
  than a binding library, for fidelity to git's own worktree semantics and error messages. *(To be
  confirmed in architecture — `pygit2`/`GitPython` are alternatives; CLI is the conservative pick
  for worktree edge cases.)*
- **Process topology:** `[AMENDED 2026-06-22]` one **long-lived, separately-launched, machine-global**
  process (not one per repo) hosting **both** the MCP endpoint and the dashboard HTTP server, with a
  single `asyncio` event loop running the MCP transport plus a small ASGI app (e.g. Starlette/FastAPI,
  or stdlib) for the dashboard. It orchestrates git across **multiple repos** — each `run_git` call
  is `-C <repo>` for the repo the task targets. This topology is only viable under the HTTP transport
  decision below — see §2.

## 2. MCP transport decision (D10) & async constraints (R3, R4)

> **Decision (D10, from technical-feasibility review 2026-06-19):** the server uses the
> **Streamable HTTP transport**, runs as a **separately-launched, long-lived process** on
> `127.0.0.1`, and is registered in Claude Code as an **HTTP/URL** MCP server (not a stdio
> `command`). This is the load-bearing decision — most other technical findings resolve from it.

- **Why HTTP, not stdio.** The MCP spec defines two transports with *opposite* lifecycle models:
  - **stdio** — the client launches the server as a *subprocess* and kills it when the session ends.
    The server cannot be "left running with a dashboard open" (UJ-1 entry state), and each of the
    2–4 parallel agents would spawn its *own* subprocess, all contending for the same dashboard port.
    **Rejected** precisely because it ties server lifetime to one client session and forbids a shared
    persistent dashboard.
  - **Streamable HTTP** — the server is an independent long-lived process handling multiple client
    connections. This is the only model that supports the persistent dashboard + single shared state
    the vision requires. **Chosen.** Cost: the developer starts the server separately and registers
    it by URL `[AMENDED 2026-06-22]` **once, globally** (a single HTTP MCP server shared across all
    projects — no longer one registration per repo).
- **Repo binding `[AMENDED 2026-06-22]`.** No `--repo` flag. The server is **machine-global**: a
  repo becomes known when a task names it (the agent passes an **absolute repo path** in
  `create_task`). The server validates the path is a git repo, then runs `git -C <repo>` for that
  task's worktrees. One global server is registered as a single HTTP MCP server in Claude Code (shared
  across every project). Running N repos = **still one process**, tracking the union of repos
  referenced by active tasks.
- **Single-instance / port handling `[AMENDED 2026-06-22]`.** One known/configurable port for the
  **whole machine** (default 8765, fallback scan). On startup the server checks a **global** lockfile
  (XDG state dir) and/or probes the port, and either refuses with a clear message or attaches —
  rather than dying on `EADDRINUSE`. The dashboard URL is printed on startup so the developer can find
  it. (Single-instance is now per-machine, not per-repo.)
- **Protocol-synchronous ≠ runtime-blocking.** Tools return well before the transport timeout
  (~5 min) but must **not** block the event loop. All `git` shell-outs use
  `asyncio.create_subprocess_exec` (or a thread/executor) so the dashboard and concurrent tool calls
  stay responsive — `git worktree add` on a large/cold repo can take seconds and must not stall the
  loop. No mature native async/task primitive exists yet (SEP-1391/1686 are emerging, not relied
  upon); any future long work is fire-and-forget, with status polled via a separate tool rather than
  blocking.
- **Origin validation (security).** The HTTP transport spec mandates validating the `Origin` header
  on the MCP endpoint; apply the same to dashboard routes and reject non-localhost origins to block
  DNS-rebinding. (See PRD NFR-Security/Locality.)
- **Small tool surface (~7 tools planned).** Clients cap tool counts (e.g. Cursor ~40). Keep it
  tight; resist one-tool-per-option sprawl.

## 3. Candidate MCP tool schemas (capability-level → FR map) `[AMENDED 2026-06-22]`

Concrete names/shapes for the architecture pass; not frozen. The surface is now **task-centric**:
`create_task` replaces the old `create_worktree`+`register_task` pair, taking a **set of repos** and
creating one worktree per repo in one atomic call. **5 tools** (down from 6).

| Tool | FR | Inputs (sketch) | Output (sketch) |
|------|----|-----------------|-----------------|
| `create_task` | FR-1/FR-4 | `task_name`, `description`, `repos: [abs_path, …]` (1+), `base_ref?` (default each repo's HEAD), `status?`=`running` | `{ task_id, status, worktrees: [{ repo_path, worktree_path, branch }] }` or structured error (all-or-nothing across repos) |
| `list_worktrees` | FR-2 | `repo?`, `task_id?` | `[{ repo_path, worktree_path, branch, task_id?, status? }]` (across all tracked repos) |
| `remove_worktree` | FR-3 | (`task_id`+`repo`) or `path`, `delete_branch?`, `force?` (dirty-tree), `force_unmerged_branch?` (distinct — see §3a) | `{ removed: true }` or error |
| `update_task` | FR-5/6 | `task_id`, `status?` ∈ {running,blocked,review,done} `[AMENDED 2026-06-22b]`, `description?` | `{ task_id, status, updated_at }` |
| `list_tasks` | FR-7 | `status?`, `repo?` | `[{ task_id, description, status, created_at, updated_at, worktrees: [{ repo_path, branch, worktree_path }] }]` |

The git-derived view (FR-12) is internal, not a tool — see §4. **Deferred (post-v1):** an
`add_worktree(task_id, repo)` tool to attach a repo to an existing task incrementally (v1 fixes the
repo set at `create_task`).

## 4. Persistence & concurrency (A2, FR-12, FR-4)

- **Store `[AMENDED 2026-06-22]`:** small **SQLite** DB in a **machine-global** location (candidate:
  XDG state dir, e.g. `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`) — **not** per-repo,
  since one server now spans repos. Enable **WAL mode** and a `busy_timeout`.
- **Concurrency model.** Because the transport is a single long-lived HTTP process (D10), **all
  writes flow through one process** and are serialized on its event loop. `[AMENDED 2026-06-22]`
  Enforce "at most one active task per `<task>` slug" (FR-4) with the **`task.task_id` primary key**
  plus a status check (the slug IS the active-task key; a new task with a live slug conflicts), not an
  application-level read-then-write, so the invariant holds atomically under near-simultaneous
  `create_task` calls.
- **Per-repo mutation mutex `[AMENDED 2026-06-22, decided 2026-06-22]`.** The global lockfile guards
  the **process singleton only**, NOT per-repo mutation safety. Two concurrent calls can touch the
  same repo (`repos=[x,y]` and `[y,z]`), and the mutation pool (sem=4) is a concurrency limiter, not a
  lock. An in-process **async mutex keyed by `repo_path`** serializes mutations to the same repo
  (read/refresh ops do not take it) — without it concurrent same-repo `git worktree add` races.
- **`create_task` atomicity (error-safe) `[decided 2026-06-22]`.** Preflight all repos (git-repo?
  branch/dir free? base ref?) before mutating any; provision worktrees, then commit `task` +
  `task_worktree` rows **last** in one SQLite transaction on full success. On a later-repo failure,
  compensate (`worktree remove --force` + `branch -D agent/<task>`) and write no rows; if a
  compensation itself fails, raise **`RollbackIncomplete`** naming the orphaned repos (never silent).
  Full crash-safety is a documented v1 non-goal (residue = no-DB-row orphan worktree, surfaced by
  derive-on-read; no startup reconciliation engine).
- **Model `[AMENDED 2026-06-22]`:** two tables — a task and its per-repo worktree links:
  ```sql
  CREATE TABLE task (
    task_id     TEXT PRIMARY KEY,        -- the <task> slug
    description TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('running','blocked','review','done')), -- [AMENDED 2026-06-22b] +review
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
  );
  CREATE TABLE task_worktree (
    task_id       TEXT NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
    repo_path     TEXT NOT NULL,         -- absolute repo path
    branch        TEXT NOT NULL,         -- agent/<task>
    worktree_path TEXT NOT NULL,
    PRIMARY KEY (task_id, repo_path)     -- one worktree per repo per task
  );
  ```
  A `<task>` slug may be reused over time (sequential reuse after `done`); at most one non-`done`
  task per slug. `git worktree list --porcelain` (per repo) is the source of truth for worktree
  existence — the DB stores task records and their per-repo worktree linkage, not a duplicate
  worktree registry.
- **Git-derived view `[AMENDED 2026-06-22]`:** for each distinct `repo_path` in `task_worktree`, run
  `git -C <repo> worktree list --porcelain` and left-join annotations by `(repo, branch)`, both on
  tool calls and on a periodic background tick (decoupled from dashboard polling — see §5). Worktree
  links gone from git → that task's link to the repo is detached/closed; worktrees present with no
  task → shown task-less; stale metadata and half-removed dirs handled with `git worktree prune`
  semantics. The fan-out across repos is bounded by the read-pool semaphore.

## 5. Dashboard mechanism (A1, FR-8–10)

- **Served by the same long-lived process**, bound `127.0.0.1` (A3, D10). Read-only — no mutating
  routes. Validates `Origin` (see §2).
- **Live refresh:** **polling is the v1 baseline** — a short-interval poll of a `/state` JSON
  endpoint served from reconciled *cached* state (no git shell-out per poll). Target: state changes
  visible **≤ 3s** (NFR-Performance) `[decided 2026-06-22]` **bounded to ≤ 15 tracked repos** — the
  refresh fans `git worktree list` across every tracked repo under the read pool, so a slow repo
  degrades **that repo only** (rendered "unavailable"/last-known), never the whole board. SSE
  server-push is a vNext enhancement — it needs the long-lived process (now guaranteed) but adds
  connection-lifecycle edge cases not worth it for v1.
- **UI `[AMENDED 2026-06-22; 2026-06-22b]`:** minimal — a board of **three active status columns**
  (Running | Blocked | Review) with `done` as a foldable `✓ N done` count below, grouped **by task**;
  each task card shows its description, status (column + colored left bar + per-card glyph), an
  optional reason badge (blocked "needs input" / review "awaiting review" — never "merge"), and its
  per-repo worktrees (repo, branch, path). Blocked is the emphasized alarm state. No motion;
  diff-and-patch poller (stable render); self-contained (no external assets). Full visual + behavioral
  contract: UX spec at `ux-designs/ux-dev-helper-mcp-2026-06-22/` (`DESIGN.md` + `EXPERIENCE.md`). No build-tooling-heavy
  SPA needed for v1.

## 6. Worktree conventions (A4 — RESOLVED 2026-06-19)

- **Location:** sibling directory `<repo-parent>/<repo>.worktrees/<task>/` — outside the repo tree.
  Chosen over in-repo `.claude/worktrees/` (Claude native) and `.dev-helper-mcp/worktrees/` to avoid
  nested-worktree recursion and IDE/file-watcher confusion, and to give one folder to bulk-clean.
- **Branch naming:** `agent/<task>` — namespaced so branches are obviously attributable to this tool
  and unlikely to collide with the developer's own branches.
- **`<task>` slug:** derived from the caller-supplied task name (slugified: lowercase, hyphenated).
  Slug rules to pin in architecture: max length, unicode handling, collapsing
  duplicate/leading/trailing hyphens, rejecting empty/reserved names. `[AMENDED 2026-06-22]` The slug
  is the **task identity** and is the same `agent/<task>` branch name used in **every** repo the task
  spans. **Collision policy = reject** with a structured "name already in use, pick another" error
  when the branch *or* directory already exists in **any** requested repo, **not** silent `-2`
  suffixing, so agent behaviour stays predictable; the create is **all-or-nothing across the repo
  set**. A pre-existing untracked target directory in any repo is detected and reported, never passed
  through as a raw git error (see PRD FR-1).
- **State DB location `[AMENDED 2026-06-22]`:** **machine-global**, not in-repo — the server spans
  repos, so the DB lives in an XDG state dir (e.g.
  `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/state.db`) alongside the global `server.lock`.
  Worktrees still live as siblings of each repo. Confirm exact path in architecture.

## 3a. `remove_worktree` force semantics (FR-3)

Two **distinct** safety concerns — never collapse onto one `force` flag:
- `force` → dirty-worktree removal, maps to `git worktree remove --force`. Define "uncommitted
  changes" precisely: tracked-dirty + staged count; state whether untracked files count.
- `force_unmerged_branch` → a separate, louder guard for `delete_branch` when the branch has unmerged
  commits (maps to `git branch -D` vs `-d`). The tool first surfaces "branch has N unmerged commits."
  This is the real data-loss path: because merge-back is out of scope, branches accumulate unmerged
  work.

## 7. Rejected / deferred alternatives (rationale preserved)

- **Container-based isolation (à la container-use/Dagger).** Rejected for v1 — heavier (needs
  Docker), and the user wants a light Linux-friendly local tool. Plain worktrees chosen.
- **TUI instead of web dashboard.** Rejected — user explicitly wants a web dashboard; also
  differentiates from the many TUI competitors (recon, agent-deck, claude-tmux).
- **stdio MCP transport.** Rejected (D10) — ties server lifetime to a single Claude Code session and
  spawns one subprocess per agent, which is incompatible with a persistent, shared, single-instance
  dashboard. Streamable HTTP chosen instead. (See §2.)
- **Tool launches/supervises agents.** Rejected for v1 (D3) — and it would force long-running
  process supervision that fights MCP's synchronous tool model (R3).
- ~~**Multi-repo from day one.** Deferred (D8).~~ `[AMENDED 2026-06-22]` **Now adopted for v1** — a
  single global server with multi-repo tasks. The state model stays simple (two small tables; the
  derive-on-read projection just fans `git worktree list` out across the task's repos). The
  rejected-alternative here is instead the *old* one-server-per-repo topology (N processes, N ports),
  dropped because it cannot show a developer everything in flight across repos in one glance.
- **Building on Claude Code native `--worktree` only.** Native creation is the baseline; the value
  add is task tracking + dashboard + agent self-service, which native does not provide (R2).

## 8. Landscape references (for context)

Claude Squad, Conductor (macOS-only), Crystal→Nimbalyst (Electron), Vibe Kanban, Uzi, recon,
agent-deck, container-use (Dagger), Claude Code native `--worktree`. Full digest captured during
Discovery research; key takeaways folded into PRD §1 and the decision log (R1–R5).
