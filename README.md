# dev-helper-mcp

A single, machine-global MCP server that lets an agent self-serve multi-repo git
tasks over Streamable HTTP on localhost.

This story (1.1) is the **walking skeleton**: transport + Origin security +
scaffold + the enforced quality gate. No real git, DB, tools, or dashboard yet.

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages the Python 3.12 toolchain and venv)

## Run

```sh
uv run dev-helper-mcp
```

The server binds `127.0.0.1` on the first free port in `8765`→`8775` and prints
the dashboard URL on startup. An MCP client connects over Streamable HTTP at:

```
http://127.0.0.1:<port>/mcp
```

A trivial `ping` health tool is registered to prove the handshake end-to-end.
(`--port N` forces an exact port; full lifecycle / `stop` semantics arrive in a
later story.)

## Security

- Binds loopback only — never `0.0.0.0`.
- An Origin-validation middleware rejects any request whose `Origin` header is
  present and not in `{http://127.0.0.1:<port>, http://localhost:<port>}` with a
  `403`. Requests with no `Origin` (non-browser MCP clients) are allowed.

## Development

### One-time: install the enforced quality gate

This repo ships a tracked pre-commit hook that **blocks commits** on any lint,
format, or test failure (the v1 regression gate — there is no CI). Enable it
once per clone:

```sh
git config core.hooksPath .githooks
```

(It is wired via `core.hooksPath` rather than `.git/hooks` so it survives the
`agent/<task>` worktrees this tool creates, which share one `.git`.)

On every commit the hook runs:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not slow"
```

### Tests

```sh
uv run pytest                 # full suite (includes the slow real-port smoke test)
uv run pytest -m "not slow"   # fast suite (what the pre-commit hook runs)
```
