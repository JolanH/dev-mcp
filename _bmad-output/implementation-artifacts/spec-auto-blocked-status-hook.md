---
title: 'Auto-report blocked/running to the dashboard via a Claude Code hook'
type: 'feature'
created: '2026-06-29'
status: 'done'
baseline_commit: 'b04b18fc1c08255f65f890fc7d3c01242cf71bfc'
context: ['{project-root}/_bmad-output/project-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The dashboard's `blocked` column is purely a projection of each task's persisted `status`, and `blocked` is only ever written by an explicit `update_task` call (self-report, by design). The server is a passive tracker with no channel into Claude Code's runtime, so when an agent pauses on a question/permission prompt and waits for the operator, nothing flips the task to `blocked` — it stays in RUNNING and the operator never sees "needs you."

**Approach:** Ship a best-effort `dev-helper-mcp hook <blocked|running>` CLI subcommand that the operator wires into their **global** `~/.claude/settings.json`: `Notification` (idle/permission/elicitation prompts) → `blocked`, `UserPromptSubmit` (operator answered) → `running`. The subcommand reads the hook's stdin JSON for `cwd`, maps the agent's worktree path (`<repo>.worktrees/<slug>`) back to its task slug, and writes the status straight to the store via the existing `core.update_task`. The running server's background refresher surfaces the change on the dashboard within one refresh interval (≤3s) — no live-server round-trip needed.

## Boundaries & Constraints

**Always:**
- Reuse `core.tasks.update_task` (transition validation) + `store.Store.open` (the only SQLite opener); resolve the DB via `config.default_db_path()`.
- Slug resolution is **pure path arithmetic** (no git, no FS I/O) — the inverse of `config.worktree_path_for`: find the cwd ancestor whose parent dir name ends with `WORKTREE_DIR_SUFFIX` (`.worktrees`); that segment is the slug.
- Only manage the running↔blocked pair: `blocked` action flips **only** `running`→`blocked`; `running` action flips **only** `blocked`→`running`. Never touch `review` or `done`.
- The hook is **best-effort and silent on failure**: every path (no task slug in cwd, task untracked, DB missing, illegal transition, any exception) exits **0** and writes at most a single stderr line. A hook must never block, slow, or error an agent's turn.

**Ask First:**
- Adding any new HTTP endpoint, MCP tool, or talking to the live server over the socket (the chosen design writes via the store + relies on the refresher — do not switch to an HTTP/MCP-client path without approval).
- Auto-installing/editing the operator's `~/.claude/settings.json` (this spec only *documents* the snippet; it does not write user config).

**Never:**
- No `mcp`/`starlette`/`uvicorn` import added to core; no direct SQLite open outside `store.py`; no `subprocess`/git call in the resolution path.
- Not solving subagent (`SubagentStop`) tracking, multi-session disambiguation, or `review`-state notifications — out of scope (note as limitations).
- No persisting of derived state; no change to the projection/render/`/state` contract.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Block a running task | stdin `{cwd:".../tmms.worktrees/dark-theme/src"}`, `hook blocked`, task status `running` | task → `blocked`; exit 0 | N/A |
| Resume a blocked task | same cwd, `hook running`, task status `blocked` | task → `running`; exit 0 | N/A |
| Notification while in review/done | `hook blocked`, task status `review` or `done` | no write; exit 0 | N/A |
| Resume when not blocked | `hook running`, task status `running`/`review`/`done` | no write; exit 0 | N/A |
| cwd not a task worktree | stdin `{cwd:"/code/tmms"}` (main repo) | no slug resolved; no write; exit 0 | N/A |
| Slug not tracked / DB missing | cwd resolves a slug with no DB row (or no `state.db`) | no write; exit 0 | swallow `TaskNotFound`/open error, 1 stderr line |
| Malformed / empty stdin | no JSON on stdin | fall back to `os.getcwd()`; otherwise as above | swallow parse error |
| cwd is worktree root | `{cwd:".../tmms.worktrees/dark-theme"}` | slug `dark-theme` resolved | N/A |

</frozen-after-approval>

## Code Map

- `src/dev_helper_mcp/config.py` — add pure `slug_from_worktree_cwd(cwd)` next to `worktree_path_for` (reverse map using `WORKTREE_DIR_SUFFIX`); returns slug or `None`.
- `src/dev_helper_mcp/cli.py` — add `hook` to the flat parser + an optional positional state (`blocked`/`running`); dispatch in `main()`: read stdin JSON → resolve slug → run the async store update; best-effort wrapper.
- `src/dev_helper_mcp/core/tasks.py` — reuse `update_task` (no change).
- `src/dev_helper_mcp/store.py` — reuse `Store.open` / `get_task` / `update_task` (no change).
- `README.md` — document the `hook` subcommand + the global `~/.claude/settings.json` wiring snippet and its limitations.
- `tests/test_cli.py` — extend with slug-resolution + hook-behavior cases.

## Tasks & Acceptance

**Execution:**
- [x] `src/dev_helper_mcp/config.py` -- add `slug_from_worktree_cwd(cwd: str | Path) -> str | None` -- pure inverse of `worktree_path_for`; the single source of the path→slug rule, unit-testable without FS/git.
- [x] `src/dev_helper_mcp/cli.py` -- add `hook <blocked|running>` subcommand: parse, read stdin JSON (`cwd`, fallback `os.getcwd()`), resolve slug, and in an `asyncio.run` open the store, `get_task`, and conditionally `core.update_task` only for running↔blocked; wrap the whole command so it always exits 0 and logs at most one stderr line.
- [x] `README.md` -- document the subcommand, the global settings.json snippet (`Notification` idle/permission/elicitation → blocked, `UserPromptSubmit` → running), the ≤3s dashboard lag, and the subagent/multi-session limitations.
- [x] `tests/test_cli.py` -- unit-test `slug_from_worktree_cwd` (root, subdir, main-repo, non-worktree, nested) and the `hook` command edge cases from the I/O Matrix using a `tmp_path` `state.db` via `Store.open` (NEVER the project repo); assert conditional transitions and always-exit-0.

**Acceptance Criteria:**
- Given a tracked task in `running` whose worktree contains the hook's `cwd`, when `dev-helper-mcp hook blocked` runs with that cwd on stdin, then the task row becomes `blocked` and a running dashboard reflects it after the next refresh (≤3s).
- Given the operator answers the prompt, when `UserPromptSubmit` fires `dev-helper-mcp hook running`, then a `blocked` task returns to `running` while `review`/`done` tasks are left untouched.
- Given any failure mode (no slug, untracked, DB missing, illegal transition, bad stdin), when the hook runs, then it exits 0 and does not raise — never disrupting the agent's turn.
- Given the project's git-safety rule, when the new tests run, then every store/DB operation targets a `tmp_path` DB and no git or project-repo mutation occurs.

## Design Notes

Why store-write + refresher rather than an MCP/HTTP call to the live server: `cache.refresh()` re-reads `store.list_tasks()` every tick (`cache.py:96`) and the refresher runs on the configured interval, so a committed row change appears on `/state` within ≤3s with zero new server surface. Two-writer SQLite is safe here (WAL + `busy_timeout`, `update_task` is a pure row mutation — no worktree/git).

Slug resolution (pure): walk `cwd` and its ancestors; return the first segment `p.name` where `p.parent.name.endswith(".worktrees")`; else `None`.

Hook event mapping (from Claude Code hook semantics): `Notification` supports matchers `idle_prompt` / `permission_prompt` / `elicitation_dialog` — the "agent needs you" signals → `blocked`; `UserPromptSubmit` (operator submitted an answer) → `running`. Exact matcher names are version-sensitive; the operator-facing snippet is documentation, and the CLI is matcher-agnostic (it just sets a state), so a taxonomy drift degrades to "no auto-block," never to a crash.

## Verification

**Commands:**
- `uv run pytest tests/test_cli.py -q` -- expected: all pass, including new slug-resolution + hook cases.
- `uv run ruff check . && uv run ruff format --check .` -- expected: clean (the pre-commit gate).
- `uv run pytest -m "not slow" -q` -- expected: full fast suite green (no regression).

**Manual checks:**
- With a running server and a real task, `echo '{"cwd":"<worktree-subdir>"}' | dev-helper-mcp hook blocked` then reload the dashboard within a few seconds — the card moves to the Blocked column; `hook running` moves it back.

## Suggested Review Order

**The bridge (design intent)**

- Start here: the hook orchestrator — best-effort, always-exit-0, never-hang contract in one place.
  [`cli.py:238`](../../src/dev_helper_mcp/cli.py#L238)

- The transition guard — the running↔blocked pair, never touching review/done.
  [`cli.py:235`](../../src/dev_helper_mcp/cli.py#L235)

- Store write + DB-existence short-circuit (no side-effect DB creation); the refresher surfaces it.
  [`cli.py:328`](../../src/dev_helper_mcp/cli.py#L328)

**Slug resolution**

- Pure inverse of `worktree_path_for`; lexical `normpath`, no FS/git.
  [`config.py:127`](../../src/dev_helper_mcp/config.py#L127)

**Robustness (from review)**

- Time-bounded stdin read — an idle/never-closed stdin can't hang the turn.
  [`cli.py:312`](../../src/dev_helper_mcp/cli.py#L312)

- Guarded diagnostics + realpath cwd — a closed stderr pipe / symlinked worktree can't break it.
  [`cli.py:283`](../../src/dev_helper_mcp/cli.py#L283)

**Surface & docs**

- CLI parser: `hook <blocked|running>` on the existing flat parser; dispatch in `main`.
  [`cli.py:356`](../../src/dev_helper_mcp/cli.py#L356)

- Operator wiring snippet + honest limitations.
  [`README.md:124`](../../README.md#L124)

**Tests (supporting)**

- Slug-resolution matrix (root, subdir, `..`, trailing slash, bare `.worktrees`).
  [`test_cli.py:472`](../../tests/test_cli.py#L472)

- Hook behavior: conditional transitions, no-clobber, always-exit-0, no-DB-creation.
  [`test_cli.py:476`](../../tests/test_cli.py#L476)
