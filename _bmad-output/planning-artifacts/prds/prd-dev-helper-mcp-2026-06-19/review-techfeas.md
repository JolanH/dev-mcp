# Technical-Feasibility Review — dev-helper-mcp

*Adversarial review of `prd.md` + `addendum.md`. Date: 2026-06-19. Reviewer role: technical-feasibility skeptic. Findings are tagged `[critical|high|medium|low]` with location, risk, and a suggested fix.*

## Verdict

The product is buildable, but the **central architectural claim — "one process hosts both the MCP server and the long-lived dashboard" (addendum §1, PRD FR-13, NFR-MCP-Compatibility) — is in direct tension with how Claude Code launches stdio MCP servers**: a stdio server is a *subprocess the client spawns and kills per session*, so it cannot host a dashboard or state that "survives across client sessions." The PRD never picks a transport, and that one unmade decision invalidates several downstream claims (persistent dashboard, single shared state, "single repo per running server"). The task-tracking, persistence, and git-worktree features are individually feasible, but the lifecycle/transport mismatch and the multi-process concurrency story must be resolved before architecture, or the whole "live dashboard from the same process" vision quietly fails.

---

## Findings

### [critical] Transport choice is undecided, and the two options are mutually exclusive with the "one process / persistent dashboard" claim
**Location:** addendum §1 (Process topology), §2 (Transport: "stdio is the natural local default… an HTTP transport is an alternative"); PRD FR-13, NFR-MCP-Compatibility, A1.

**Risk:** The MCP spec defines two transports with *opposite* lifecycle models:
- **stdio:** "The client launches the MCP server as a subprocess… Close stdin, terminate subprocess." The server lives and dies with one Claude Code session. It cannot be "left running with a dashboard tab open" (UJ-1 entry state) — when the session ends, the process (and dashboard, and in-memory state) is killed. Worse, each of the 2–4 parallel agents Claude Code runs would spawn its *own* subprocess instance, so there is no single process hosting one dashboard — there are N of them, each trying to bind the same dashboard port (see next finding).
- **Streamable HTTP:** "the server operates as an independent process that can handle multiple client connections." This *does* support a long-lived server + dashboard + shared state, but it is **not** how Claude Code typically auto-launches a local MCP server, and it requires the developer to start the server separately and register it by URL.

The PRD's vision (long-lived dashboard, persistent state across sessions, single shared picture) only works under the HTTP model, but the addendum calls stdio "the natural local default." These cannot both be true. The PRD treats this as a deferrable architecture detail; it is actually the load-bearing decision.

**Suggested fix:** Decide now, in the PRD/addendum, that the server is a **separately-launched, long-lived process using Streamable HTTP transport on `127.0.0.1`**, registered in Claude Code as an HTTP/URL MCP server (not a stdio `command`). Rewrite FR-13, A1, and UJ-1's entry state to say "the developer starts the server once; Claude Code connects to it over HTTP." Explicitly state stdio is rejected *because* it ties server lifetime to a single client session and forbids a shared persistent dashboard. (If stdio is kept for any reason, the dashboard + state must move to a *separate* daemon, which contradicts "one process hosts both.")

---

### [critical] Port binding / single-instance contention is unspecified and breaks under stdio or multiple servers
**Location:** PRD FR-13 ("serves both… from one process bound to `127.0.0.1`"), A5; addendum §1, §5.

**Risk:** Nothing defines what happens when a *second* server process tries to start (or is spawned) against the same repo. The dashboard binds a fixed localhost port; a second instance will fail with `EADDRINUSE` or silently not serve. Under the stdio model this is guaranteed to happen (one subprocess per agent session). Even under the HTTP model, the developer can accidentally start two. There is no defined port (fixed vs. ephemeral vs. configurable), no "already running, attach instead" behavior, and no discovery mechanism for the dashboard URL.

**Suggested fix:** Specify single-instance semantics: a known/configurable port, a lockfile or "is a server already bound to this repo?" check on startup, and a clear error or attach-to-existing behavior. Document how the developer learns the dashboard URL (printed on startup). Make "one server per repo" an *enforced* invariant, not just an assumption (A5).

---

### [high] "Single repo per running server" (A5) mismatches Claude Code's per-project MCP config model
**Location:** PRD Glossary ("one repo per running server"), FR-13, A5; Non-Goals (multi-repo); addendum §1.

**Risk:** Claude Code registers MCP servers per-project (project-scoped `.mcp.json` / settings, or user-scoped). A project-scoped stdio entry would spawn a server with the project as cwd — which *accidentally* gives you per-repo binding, but reintroduces the per-session-subprocess problem (critical #1). A user-scoped server, conversely, is shared across all projects and has no inherent notion of "the one repo." So "the server is pointed at one repo per run" requires an explicit mechanism (CLI arg / env var / cwd) that the PRD assumes but never defines, and that mechanism interacts with whichever transport is chosen. If HTTP is chosen, the developer must run one server *per repo* on *distinct ports* and register each by URL in the matching project — none of which is described.

**Suggested fix:** Define how the server is told which repo it serves (e.g. `dev-helper-mcp --repo <path>`, or cwd at launch), and document the registration recipe for each repo (HTTP URL + port per repo, added to that project's MCP config). State explicitly that running N repos = N server processes on N ports. Reconcile this with the "single shared dashboard" mental model in UJ-1.

---

### [high] Multi-instance concurrent SQLite access is asserted as safe but not designed
**Location:** addendum §4 ("SQLite preferred for concurrent reads from the dashboard while tools write"); PRD FR-12, A2.

**Risk:** The addendum justifies SQLite for "concurrent reads from the dashboard while tools write" — but the dashboard and tool writes are *in the same process*, so that is not the hard case. The hard case (which the transport ambiguity makes real) is **multiple server processes** — multiple stdio subprocesses from multiple agents, or two HTTP servers — writing the same `state.db` concurrently. SQLite's default rollback-journal mode serializes writers with a database-level lock and will throw `SQLITE_BUSY` under contention; WAL mode helps readers but still single-writer. Without a stated concurrency model, parallel `register_task`/`update_task` calls from 2–4 agents can collide, and the "at most one active task per worktree" invariant (FR-4) has a check-then-write race (two agents register against the same worktree near-simultaneously, both pass the check). FR-4's conflict guarantee is not enforceable without a DB-level unique constraint or transaction.

**Suggested fix:** Once transport is fixed to single-long-lived-HTTP (critical #1), state that all writes go through that one process, serialized on the asyncio loop — that resolves most of it. Additionally: enable WAL mode, set a busy_timeout, and enforce "one active task per worktree" with a partial unique index (`UNIQUE(worktree_path) WHERE status != 'done'`) rather than an application-level read-then-write. If multi-process remains possible, say so and design for `SQLITE_BUSY` retries explicitly.

---

### [high] Async/blocking violation: shelling out to `git` inside an asyncio MCP handler
**Location:** addendum §1 ("asyncio event loop running the MCP transport plus a small ASGI app"), §1 Git access ("shell out to the `git` CLI"); PRD NFR-MCP-Compatibility ("no long-blocking work behind a tool call"), NFR-Performance.

**Risk:** `git worktree add` is fast but not instant (it checks out a full working tree; on a large repo or cold cache it can take seconds), and a *synchronous* `subprocess.run` call on the asyncio event loop **blocks the entire loop** — including the dashboard's `/state` SSE/polling responses and all other concurrent tool calls. The PRD/addendum claim "synchronous, short tool calls" but conflate *MCP-protocol-synchronous* (returns before the ~5min timeout) with *runtime-synchronous* (blocks the loop). With 2–4 agents and a worktree-create checking out a sizeable tree, a blocking call stalls the whole server.

**Suggested fix:** Mandate `asyncio.create_subprocess_exec` (or run blocking git in a thread/executor) for all git shell-outs so the event loop stays responsive. Add an NFR or architecture note: "no synchronous subprocess call on the event loop." Note that `git worktree add` on a large repo is the worst case and validate latency against NFR-Performance's sub-second target (it may not hold for big repos — soften the target or scope it to "typical repo").

---

### [medium] `<task>` slug → branch/directory collision handling is hand-waved
**Location:** PRD FR-1; addendum §6 ("Architecture to define exact slug rules and collision handling (e.g. if `agent/fix-auth` exists)").

**Risk:** Two different task names can slugify to the same slug (`"Fix Auth"` and `"fix-auth"` → `fix-auth`), producing the same branch `agent/fix-auth` and the same directory `<repo>.worktrees/fix-auth/`. FR-1's testable consequence handles "branch already exists" by refusing — but the *directory* collision is separate: a stale directory from a prior removed-but-not-cleaned worktree, or a slug clash, makes `git worktree add` fail on path existence with a raw git error, not the "clear structured error" the PRD promises. Slug rules (length cap, unicode, empty/duplicate-hyphen, reserved names, what happens on collision: reject vs. suffix `-2`) are explicitly punted to architecture, but they materially affect FR-1's "done."

**Suggested fix:** Define the slug contract in the PRD/addendum: normalization rules, max length, and the **collision policy** (recommend: reject with a structured "slug already in use, pick another name" error rather than silent suffixing, to keep agent behavior predictable). Specify that a pre-existing target directory (not git-tracked) is detected and reported, not passed through as a raw git failure.

---

### [medium] Reconciliation is under-specified for the "stale directory" and "manual branch" cases, and its trigger is lazy
**Location:** PRD FR-12 ("reconciled… on startup and on every `list_*`"); addendum §4, §5; SM-4 ("never shows a contradicted state").

**Risk:** Reconciliation runs "on startup and on every `list_*`." But the dashboard auto-refresh (FR-9) hits a `/state` endpoint, which must therefore trigger reconciliation on every poll — meaning a `git worktree list` shell-out on every few-second refresh (back to the blocking-subprocess concern, and constant git invocations). Also, the spec covers "worktree gone from git → task detached" but not the inverse the dashboard will hit: a worktree created out-of-band via raw `git worktree add` (no task), or a `.worktrees/` directory left behind after `git worktree remove --force` failed mid-way (git's known `worktrees/` metadata vs. on-disk dir drift). FR-2 says the list "reflects actual on-disk git worktrees" but the source of truth (`git worktree list` porcelain vs. scanning the sibling dir) isn't pinned, and the two disagree exactly in the failure cases that matter.

**Suggested fix:** Pin `git worktree list --porcelain` as the single source of truth for worktree existence (not directory scanning), and `git worktree prune` semantics for stale metadata. Decouple reconciliation cadence from dashboard polling (e.g. reconcile on tool calls + a periodic background tick, and serve the dashboard from cached state) to avoid a git shell-out per refresh. Specify behavior for out-of-band-created worktrees (show with no task) and half-removed directories.

---

### [medium] FR-3 force semantics: "uncommitted changes" detection and branch-deletion safety are incomplete
**Location:** PRD FR-3 (refuse on uncommitted changes without `force`; branch deletion only when requested); addendum §3 (`remove_worktree`).

**Risk:** "Uncommitted changes" is ambiguous: does it include untracked files? staged-but-uncommitted? unpushed commits on the branch (which `git worktree remove` does *not* check, but losing them on `delete_branch` is the real data-loss path)? `git worktree remove` refuses on a dirty tree by default and needs `--force`; but `git branch -d` refuses to delete an unmerged branch, requiring `-D` — so a user passing `force:true` + `delete_branch:true` can silently destroy unmerged commits. The PRD's safety guardrail ("refuse… on uncommitted changes") doesn't cover the unmerged-branch-deletion case, which is the more dangerous one given merge-back is out of scope (branches accumulate unmerged work).

**Suggested fix:** Separate the two force concerns: `force` for dirty-worktree removal (maps to `git worktree remove --force`) and a *distinct, louder* guard for deleting an **unmerged** branch (require an explicit flag and surface "branch has N unmerged commits"). Define "uncommitted changes" precisely (tracked dirty + staged; state whether untracked counts). Never map a single `force` flag onto both `git worktree remove --force` and `git branch -D`.

---

### [medium] Dashboard auto-refresh (FR-9 / A1) under SSE conflicts with the read-only + stdio realities
**Location:** PRD FR-9, A1; addendum §5 ("SSE preferred… polling fallback").

**Risk:** SSE "server pushes on state change" requires a long-lived server holding open connections — fine under HTTP transport, impossible under stdio (process dies with the session). It also requires the write path (tool calls) and the SSE push path to share process/state, which is exactly the contested topology. If the fallback (short-interval polling of `/state`) is used, see medium #2 (reconciliation per poll). FR-9's testable consequence ("change visible within a bounded interval with no user action") is only achievable once the long-lived-process question is settled.

**Suggested fix:** Make polling the v1 baseline (simpler, no connection-lifecycle edge cases) with SSE as a vNext enhancement, and gate both on the resolved long-lived-HTTP topology. State the concrete refresh interval target so FR-9 is testable (e.g. "≤ 3s").

---

### [low] "Server bound to 127.0.0.1, no auth" omits the DNS-rebinding / Origin-validation guard the MCP spec requires
**Location:** PRD NFR-Security/Locality, A3, FR-13; addendum §5.

**Risk:** The MCP Streamable-HTTP spec explicitly warns: localhost-bound HTTP MCP servers **MUST validate the `Origin` header** to prevent DNS-rebinding attacks where a malicious website drives the local server. The PRD treats "localhost bind, no auth, single-user" as sufficient. For the dashboard (a browser page) plus an HTTP MCP endpoint, a missing Origin check is a real local-attack vector, not a theoretical one.

**Suggested fix:** Add an NFR: validate the `Origin` header on both the MCP endpoint and dashboard routes; reject non-localhost origins. Cheap to implement, and the spec mandates it for the transport this design is heading toward.

---

### [low] "At most one active task per worktree" (FR-4) and `done`-then-reuse lifecycle is underspecified
**Location:** PRD FR-4 (A2), FR-6 (`done` no longer counts as active); addendum §3.

**Risk:** A worktree's task goes `done`, freeing the worktree for a "second active task" — but FR-4 says registering a second active task is a conflict. Is re-registering a *new* task against a worktree whose prior task is `done` allowed (it should be), and if so does the dashboard show one row or a history? The relationship between a worktree and its (possibly multiple, sequential) tasks over time isn't modeled. Minor, but affects the data model and dashboard rendering.

**Suggested fix:** State that "one active task per worktree" means one non-`done` task; re-registration after `done` is permitted and the dashboard shows the current active task (history optional/out-of-scope). Clarify whether `done` tasks are retained or pruned.

---

## Severity counts

- critical: 2
- high: 3
- medium: 4
- low: 2

**Total: 11 findings.**

The two criticals (transport/lifecycle decision + port/single-instance contention) are gating: they must be resolved in the PRD/addendum before the architecture workflow runs, because nearly every other finding (concurrency, reconciliation cadence, SSE, async-blocking) resolves differently depending on the transport choice.
