# Technical Addendum — dev-helper-mcp

Companion to `prd.md`. Holds the technical-how, mechanism/transport decisions, candidate tool
schemas, and rejected-alternative rationale that the PRD deliberately keeps out of its capability
narrative. This is **input for the architecture workflow**, not a final design — it captures intent
and constraints so `bmad-create-architecture` has a running start. Decisions trace to the
`.decision-log.md` IDs.

## 1. Stack decision (D5)

- **Language/runtime:** Python. Use the official MCP SDK (`mcp` / `modelcontextprotocol` Python
  package). Rationale: user preference; mature official SDK; good `git` and HTTP story.
- **Git access:** shell out to the `git` CLI (`git worktree add/list/remove`, `git branch`) rather
  than a binding library, for fidelity to git's own worktree semantics and error messages. *(To be
  confirmed in architecture — `pygit2`/`GitPython` are alternatives; CLI is the conservative pick
  for worktree edge cases.)*
- **Process topology:** one **long-lived, separately-launched** process hosting **both** the MCP
  endpoint and the dashboard HTTP server, with a single `asyncio` event loop running the MCP
  transport plus a small ASGI app (e.g. Starlette/FastAPI, or stdlib) for the dashboard. This
  topology is only viable under the HTTP transport decision below — see §2.

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
    it by URL per repo (documented recipe below).
- **Repo binding (resolves the A5/per-project-config mismatch).** The server is told which repo it
  serves explicitly — `dev-helper-mcp --repo <path>` (or cwd at launch). Running N repos = **N server
  processes on N distinct ports**, each registered as an HTTP MCP server in that project's Claude Code
  config. There is no single multi-repo server in v1 (consistent with §5 Non-Goals).
- **Single-instance / port handling.** A known or configurable port per repo. On startup the server
  checks (via a repo-keyed lockfile or a port probe) whether an instance is already bound to this
  repo, and either refuses with a clear message or attaches — rather than dying on `EADDRINUSE`. The
  dashboard URL is printed on startup so the developer can find it.
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

## 3. Candidate MCP tool schemas (capability-level → FR map)

Concrete names/shapes for the architecture pass; not frozen.

| Tool | FR | Inputs (sketch) | Output (sketch) |
|------|----|-----------------|-----------------|
| `create_worktree` | FR-1 | `branch_name`, `base_ref?` (default HEAD), `task_name?` | `{ path, branch }` or structured error |
| `list_worktrees` | FR-2 | – | `[{ path, branch, task_id?, status? }]` |
| `remove_worktree` | FR-3 | `path` or `branch`, `delete_branch?`, `force?` (dirty-tree), `force_unmerged_branch?` (distinct — see §3a) | `{ removed: true }` or error |
| `register_task` | FR-4 | `description`, `worktree` (path/branch), `status?`=`running` | `{ task_id }` or conflict error |
| `update_task` | FR-5/6 | `task_id`, `status?` ∈ {running,blocked,done}, `description?` | `{ task_id, status, updated_at }` |
| `list_tasks` | FR-7 | `status?`, `worktree?` | `[{ task_id, description, worktree, branch, status, created_at, updated_at }]` |

Reconciliation (FR-12) is internal, not a tool — see §4.

## 4. Persistence & concurrency (A2, FR-12, FR-4)

- **Store:** small **SQLite** DB in a known location (candidate: `.dev-helper-mcp/state.db` at the
  repo root, gitignored; or an XDG state dir keyed by repo path). Enable **WAL mode** and a
  `busy_timeout`.
- **Concurrency model.** Because the transport is a single long-lived HTTP process (D10), **all
  writes flow through one process** and are serialized on its event loop — removing the multi-process
  write contention the original design implied. Enforce "one active task per worktree" (FR-4) with a
  **partial unique index** (`UNIQUE(worktree_path) WHERE status != 'done'`), not an application-level
  read-then-write, so the invariant holds atomically even under near-simultaneous registrations.
- **Model:** `tasks(id, description, worktree_path, branch, status, created_at, updated_at)`; a
  worktree may have multiple tasks over time (sequential reuse after `done`), at most one non-`done`.
  `git worktree list --porcelain` is the source of truth for worktree existence — the DB stores task
  records and their worktree linkage, not a duplicate worktree registry.
- **Reconciliation:** diff DB task→worktree links against `git worktree list --porcelain`, both on
  tool calls and on a periodic background tick (decoupled from dashboard polling — see §5). Worktrees
  gone from git → their active task is detached/closed; worktrees present with no task → shown
  task-less; stale metadata and half-removed dirs are handled with `git worktree prune` semantics.

## 5. Dashboard mechanism (A1, FR-8–10)

- **Served by the same long-lived process**, bound `127.0.0.1` (A3, D10). Read-only — no mutating
  routes. Validates `Origin` (see §2).
- **Live refresh:** **polling is the v1 baseline** — a short-interval poll of a `/state` JSON
  endpoint served from reconciled *cached* state (no git shell-out per poll). Target: state changes
  visible **≤ 3s** (NFR-Performance). SSE server-push is a vNext enhancement — it needs the long-lived
  process (now guaranteed) but adds connection-lifecycle edge cases not worth it for v1.
- **UI:** minimal — a board/list of worktrees with branch, path, task description, status badge
  (`running`/`blocked`/`done` visually distinct). No build-tooling-heavy SPA needed for v1.

## 6. Worktree conventions (A4 — RESOLVED 2026-06-19)

- **Location:** sibling directory `<repo-parent>/<repo>.worktrees/<task>/` — outside the repo tree.
  Chosen over in-repo `.claude/worktrees/` (Claude native) and `.dev-helper-mcp/worktrees/` to avoid
  nested-worktree recursion and IDE/file-watcher confusion, and to give one folder to bulk-clean.
- **Branch naming:** `agent/<task>` — namespaced so branches are obviously attributable to this tool
  and unlikely to collide with the developer's own branches.
- **`<task>` slug:** derived from the caller-supplied task name (slugified: lowercase, hyphenated).
  Slug rules to pin in architecture: max length, unicode handling, collapsing
  duplicate/leading/trailing hyphens, rejecting empty/reserved names. **Collision policy = reject**
  with a structured "name already in use, pick another" error (when the branch *or* directory already
  exists), **not** silent `-2` suffixing, so agent behaviour stays predictable. A pre-existing
  untracked target directory is detected and reported, never passed through as a raw git error (see
  PRD FR-1).
- **State DB location** (separate from worktrees): since worktrees live as a sibling, the state DB
  sits in-repo under `.dev-helper-mcp/state.db` (gitignored) keyed to this repo. Confirm in
  architecture.

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
- **Multi-repo from day one.** Deferred (D8) — single-repo keeps the state model and dashboard
  simple for v1.
- **Building on Claude Code native `--worktree` only.** Native creation is the baseline; the value
  add is task tracking + dashboard + agent self-service, which native does not provide (R2).

## 8. Landscape references (for context)

Claude Squad, Conductor (macOS-only), Crystal→Nimbalyst (Electron), Vibe Kanban, Uzi, recon,
agent-deck, container-use (Dagger), Claude Code native `--worktree`. Full digest captured during
Discovery research; key takeaways folded into PRD §1 and the decision log (R1–R5).
