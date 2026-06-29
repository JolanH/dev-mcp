# dev-helper-mcp

A single, machine-global MCP server that lets an agent self-serve multi-repo git
tasks over Streamable HTTP on localhost, with a live read-only monitoring
dashboard.

One server per machine (single-instance lock) learns its repos from
`create_task` — there is **no `--repo` flag**. It exposes five MCP tools
(`create_task`, `list_worktrees`, `remove_worktree`, `update_task`,
`list_tasks`), one MCP prompt (`start_task` — a reusable create-then-implement
workflow any connecting agent can pull in), and a browser dashboard that derives
its view from `git worktree list` on every poll (no persisted derived state).

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages the Python 3.14 toolchain and venv)
- `requires-python >= 3.14` (the codebase uses 3.14-only syntax; an older
  interpreter will not import it — this is enforced by `requires-python`).

## Install

Install once as a global console command (isolated venv on your `PATH`):

```sh
uv tool install .          # from a checkout
# or build a wheel first and install that:
uv build
uv tool install dist/dev_helper_mcp-*-py3-none-any.whl
```

`pipx install .` works equivalently. After install, `dev-helper-mcp` runs from
any directory:

```sh
dev-helper-mcp            # start; scan 8765→8775 for the first free port
dev-helper-mcp --port N   # start; bind EXACTLY N or fail PortUnavailable (no scan)
dev-helper-mcp stop       # stop the running instance and release its lock
```

`python -m dev_helper_mcp` is equivalent to the `dev-helper-mcp` entry point.

## Run

```sh
uv run dev-helper-mcp     # from a checkout, without installing
```

The server binds `127.0.0.1` only and prints the dashboard URL on startup
(stdout). An MCP client connects over Streamable HTTP at:

```
http://127.0.0.1:<port>/mcp
```

The server is a single machine-global instance: a second start detects the
running one via the lockfile (`InstanceConflict`); a stale lock left by a crash
is reclaimed automatically on the next start, or cleared with
`dev-helper-mcp stop` / `--release-lock`.

## Use it from Claude Code

Once the server is running, register it with Claude Code as a Streamable-HTTP
MCP server. Pin a fixed port so the URL is stable (the default scan picks the
first free port in `8765`→`8775`, which can move between runs):

```sh
dev-helper-mcp --port 8765                                  # terminal 1: start, pinned port
claude mcp add --transport http --scope user \
  dev-helper http://127.0.0.1:8765/mcp                      # register once (all projects)
```

`--scope user` makes it available in every project (it matches the server's
machine-global nature). Verify and inspect:

```sh
claude mcp list            # from a shell — shows dev-helper as connected
/mcp                       # inside a Claude Code session — live status + tool list
```

The five tools then appear in-session as `mcp__dev-helper__create_task`,
`…_list_worktrees`, `…_remove_worktree`, `…_update_task`, `…_list_tasks`. You
drive them in **plain language** — Claude picks the tool and fills the
arguments. Small examples:

| You say | Tool Claude calls | Arguments |
| --- | --- | --- |
| "Start a task `add-oauth` to add OAuth across the api and web repos." | `create_task` | `task_name="add-oauth"`, `description="add OAuth"`, `repos=["/path/api", "/path/web"]` |
| "Start a task `add-oauth` here." (run from inside a repo) | `create_task` | `task_name="add-oauth"` — `repos`/`base_ref` default to the server's cwd repo + branch |
| "Branch it off the `release` branch instead." | `create_task` | `… base_ref="release"` |
| "What worktrees exist for `add-oauth`?" | `list_worktrees` | `task_id="add-oauth"` |
| "Show every task that's awaiting review." | `list_tasks` | `status="review"` |
| "Mark `add-oauth` as ready for review." | `update_task` | `task_id="add-oauth"`, `status="review"` |
| "Clean up the `add-oauth` worktree in the api repo and delete its branch." | `remove_worktree` | `task_id="add-oauth"`, `repo="/path/api"`, `delete_branch=true` |

What the tools do:

- **`create_task`** — create a task across one or more repos. Each repo gets an
  isolated worktree at `<repo>.worktrees/<task>/` on a new `agent/<task>` branch.
  All-or-nothing across repos. Only `task_name` is required: `description` defaults
  to empty, `repos` defaults to the git repo containing the server's current
  directory, and `base_ref` defaults to that directory's current branch. If a default
  can't be derived (the cwd isn't a git repo / isn't on a branch) the omitted argument
  errors as `NoDefaultRepo` / `NoDefaultBaseRef` — pass it explicitly.
- **`list_worktrees`** — live-derived from `git worktree list` (never a cache);
  optional `repo` / `task_id` filters. Each entry flags `orphaned` if its branch
  is gone from git.
- **`update_task`** — self-report progress. `status` is one of `running`,
  `blocked` (awaiting input), `review` (awaiting review), `done` (terminal). Any
  active state moves to any of the four; `done` is terminal — a done task can't be
  re-activated (start a fresh `create_task` of the same name to reuse the slug).
- **`list_tasks`** — task records + their per-repo worktree links; optional
  `status` / `repo` filters.
- **`remove_worktree`** — guarded removal of one task's worktree in one repo
  (other repos untouched). `delete_branch` also drops the `agent/<task>` branch;
  `force` overrides the dirty/locked-worktree guard and `force_unmerged_branch`
  the unmerged-branch guard. Removing a task's last worktree closes the task.

Every tool returns the uniform `{ok, data, error}` envelope — on failure,
`error.code` is a stable token (e.g. `BranchExists`, `ActiveTaskConflict`,
`TaskNotFound`, `DirtyWorktree`) Claude can branch on.

Open the dashboard URL printed at startup in a browser to watch tasks and
worktrees update live while you work.

### The `start_task` prompt (create-then-implement workflow)

Beyond the tools, the server exposes one **MCP prompt**, `start_task`. MCP
prompts are server-defined, client-invoked templates: any agent connected to the
server can pull the prompt in (in Claude Code it surfaces under `/mcp`, typically
as `/mcp__dev-helper__start_task`). It takes an optional `task_intent` argument —
a free-text description of what to build — and returns a workflow that tells the
agent to:

1. `create_task` (gathering only a `task_name`; let `repos`/`base_ref` default), then
2. `update_task → running` before touching code, then
3. implement the change (using the `bmad-quick-dev` skill if present), and
4. flip to `update_task → blocked` **before asking you to confirm/decide anything**,
   back to `running` on resume, ending at `review` (or `done` only on your say-so).

It keeps the dashboard honest by construction: status reflects reality at every
step, complementing the best-effort hook below.

**Single source of truth.** The prompt body is not hardcoded in Python — at fetch
time the server reads `.claude/skills/start-task/SKILL.md` (the same file the
Claude Code skill of the same name uses), strips its frontmatter, and returns the
workflow. Editing that one file updates both the `/start-task` skill (for this
repo's Claude Code sessions) and the `start_task` MCP prompt (for any connecting
agent) — no restart, no second copy to keep in sync.

> Note: prompts are *pulled*, not pushed — an agent only sees `start_task` when
> its client lists/invokes prompts. Headless agents that never enumerate prompts
> won't pick it up automatically.

### Auto-mark tasks blocked when an agent needs you

`blocked` is a self-reported status — by itself the server can't see that an
agent paused on a question or permission prompt, so a stuck agent stays in the
**Running** column. Wire a Claude Code **hook** to close that gap automatically:

```jsonc
// ~/.claude/settings.json  (global — fires in every session, any project/worktree)
{
  "hooks": {
    "Notification": [
      { "matcher": "idle_prompt",       "hooks": [{ "type": "command", "command": "dev-helper-mcp hook blocked" }] },
      { "matcher": "permission_prompt", "hooks": [{ "type": "command", "command": "dev-helper-mcp hook blocked" }] },
      { "matcher": "elicitation_dialog","hooks": [{ "type": "command", "command": "dev-helper-mcp hook blocked" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "dev-helper-mcp hook running" }] }
    ]
  }
}
```

`dev-helper-mcp hook <blocked|running>` reads the hook's `cwd` (from the stdin
JSON payload), maps the agent's worktree path `<repo>.worktrees/<slug>` back to
its task slug, and flips that task between `running` and `blocked` — and **only**
that pair (a `review` or `done` task is never touched). It is best-effort and
silent: if `cwd` isn't inside a task worktree, the slug isn't tracked, the server
isn't running, or anything else goes wrong, it exits `0` without disturbing the
agent. The change lands on the dashboard within one refresh (≤3 s).

Limitations: the `Notification` matcher names are Claude Code–version-specific
(the command itself just sets a state, so a mismatch only means "no auto-block",
never a crash); this tracks top-level sessions — parallel **sub-agents** fire
`SubagentStop`, not these events, and aren't disambiguated per task; and slug
resolution is purely lexical, so a repo whose own directory name ends in
`.worktrees` is unsupported (its ordinary subdirectories would look like task
worktrees).

## Security

- Binds loopback only — never `0.0.0.0`.
- An Origin-validation middleware (our own, outermost, over `/mcp` and the
  dashboard) rejects any request whose `Origin` header is present and not in
  `{http://127.0.0.1:<port>, http://localhost:<port>}` with a `403`. Requests
  with no `Origin` (non-browser MCP clients) are allowed.

## Logging

Stdlib `logging` to **stderr** (the dashboard URL is the one deliberate stdout
line). The level is set by `DEV_HELPER_LOG` (default `INFO`):

```sh
DEV_HELPER_LOG=DEBUG dev-helper-mcp
```

Logs are diagnostic but not leaky: a failed tool call logs its stable
`error.code`, never the user-supplied task description/annotation body.

## Development

### Quality gate (the real split)

There is **no CI in v1**; a tracked pre-commit hook is the regression gate.
Enable it once per clone (wired via `core.hooksPath` so it survives the
`agent/<task>` worktrees this tool creates, which share one `.git`):

```sh
git config core.hooksPath .githooks
```

By operator decision (2026-06-25), the hook runs **`ruff` only** (fast, every
commit); the **test suites are run manually** before pushing. The full gate is:

```sh
# enforced by the pre-commit hook:
uv run ruff check .
uv run ruff format --check .

# run manually (not in the hook):
uv run pytest -m "not slow"   # fast Python suite (in-process ASGI; tmp/:memory: DB)
node --test tests/js/         # dashboard poller diff()/patch JS tests
uv run pytest -m slow         # real-port uvicorn smoke + lock/lifecycle (run at least once)
```

### CI-readiness

The suite is CI-ready as-is — no CI config file is required for v1. Every test
uses a tmp / `:memory:` DB and the autouse `XDG_STATE_HOME` redirect plus the
`tmp_git_repo` fixture; nothing depends on an absolute local path, `$HOME`, or
interactive input. A future CI runner can run the commands above unchanged.

### Tests

```sh
uv run pytest                 # full Python suite (includes the slow real-port smoke test)
uv run pytest -m "not slow"   # fast Python suite
node --test tests/js/         # dashboard JS tests
```
