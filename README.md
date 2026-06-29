# dev-helper-mcp

A single, machine-global MCP server that lets an agent self-serve multi-repo git
tasks over Streamable HTTP on localhost, with a live read-only monitoring
dashboard.

One server per machine (single-instance lock) learns its repos from
`create_task` — there is **no `--repo` flag**. It exposes five MCP tools
(`create_task`, `list_worktrees`, `remove_worktree`, `update_task`,
`list_tasks`) and a browser dashboard that derives its view from `git worktree
list` on every poll (no persisted derived state).

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
