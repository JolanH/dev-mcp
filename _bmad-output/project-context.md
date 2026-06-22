---
project_name: 'dev-helper-mcp'
user_name: 'Dev'
date: '2026-06-22'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
existing_patterns_found: 17
status: 'complete'
rule_count: 44
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- **Python 3.14** (`.python-version` = 3.14; `requires-python = ">=3.14"`; ruff `target-version = "py314"`). Toolchain & venv via **uv**; `src/` layout, `uv_build` backend, `uv.lock` committed. (Overrides the architecture doc's "target 3.12" — operator decision, 2026-06-22.)
- **`mcp`** 1.28.0 — pinned **`>=1.28,<2`**. The `<2` bound is deliberate: official SDK **v2 (2026-07-27) is breaking**. Use `mcp.server.fastmcp.FastMCP` + `streamable_http_app()` from the 1.x line. A v2 migration stays contained to the adapter seam.
- **Transitive via `mcp`** (do NOT add as direct deps): **starlette** 1.3.1, **uvicorn** 0.49.0, **anyio** 4.14.0, **pydantic** 2.13.4.
- **`aiosqlite`** 0.22.1 — runtime dep, pinned now; DB/schema work begins Story 1.2.
- **Dev deps:** **ruff** 0.15.18 (line-length **100**, `target-version = "py314"`), **pytest** 9.1.1 (`slow` marker registered), **httpx** 0.28.1 (in-process ASGI test harness). **No `pytest-asyncio`** — keep it out (see Testing).
- Minimal-dependency posture (NFR-Simplicity): only `mcp` (+ transitive) and `aiosqlite` at runtime.

## Critical Implementation Rules

### The SDK-isolation seam — the rule most likely to be silently broken
- **Only the adapter layer may import `mcp` / `starlette` / `uvicorn`:** `server_factory.py`, `server.py`, `middleware.py`, `cli.py`, and later `tools/`, `dashboard/`, `lock.py`.
- **Core logic imports NONE of them:** `core/`, `git/`, `store.py`, `projection.py`, `cache.py`, `errors.py`, `util.py`. They take plain typed args, return plain data, raise `DevHelperError`. This is the v2-migration insurance and what makes core unit-testable with no server.
- Enforced by `tests/test_adapter_seam.py` (AST-scans `core/`, `git/`, and future `store`/`projection`/`cache` for forbidden imports). Adding a `from mcp …` to core is an automatic gate failure.

### MCP server wiring — reality differs from the architecture's literal text
- **307 fix (verified in Story 1.1):** the architecture sketches `streamable_http_path="/"` + `Mount("/mcp")`, but on **Starlette 1.3.x that 307-redirects bare `/mcp` → `/mcp/`** and the MCP SDK client does not follow POST redirects. **The working wiring is `mcp.settings.streamable_http_path = "/mcp"` + `Mount("/", app=mcp_app)`.** Clients connect to `http://127.0.0.1:<port>/mcp` with a clean 200. Do not "restore" the doc's version.
- **Lifespan is load-bearing:** the app-owned lifespan MUST wrap `async with mcp_app.router.lifespan_context(mcp_app):` or every `/mcp` request fails `"Task group is not initialized"`. Starlette does not auto-run a mounted sub-app's lifespan.
- **Tool surface (5, final):** `create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks` — verb-first `snake_case`. (Story 1.1 ships only a throwaway `ping`.)

### Async & git discipline
- **No blocking call on the event loop, ever.** Git off-loop via the single `run_git()`; DB via `aiosqlite`; unavoidable sync work via `asyncio.to_thread`.
- **Exactly one `run_git()` helper** (`git/runner.py`) — never `subprocess`/`os.system` for git anywhere else. Always `create_subprocess_exec` (never shell), always `-C <repo>`, `-z` NUL-delimited porcelain parsing, pinned env `GIT_TERMINAL_PROMPT=0` + `GIT_OPTIONAL_LOCKS=0`, on timeout `kill()` + `await wait()` (no zombies), both pipes drained.
- **Two permit pools:** read/refresh = 3s timeout, sem=2, 2s acquire (fail-fast, keep cache); mutation = ~120s, sem=4. Read/refresh git never on the `/state` poll path.
- **Per-repo async mutex** serializes same-repo mutations (`create_task`/`remove_worktree`); read/refresh ops do not take it. The global lockfile guards only the process singleton, not per-repo safety.

### Data, format & error contract
- **Every tool returns `{ok, data, error}`.** Success `{ok:true, data:…}`; failure `{ok:false, error:{code, message, details}}`. Even the `ping` seed follows this.
- **Errors are data, not protocol failures.** Core raises a typed `DevHelperError` subclass; the adapter (`tools/handlers.py`) converts. Unexpected exceptions → `{ok:false, error:{code:"Internal"}}` — **never leak a stack trace**.
- **`error.code` is a fixed, stable taxonomy:** `BranchExists, WorktreePathInUse, BaseRefNotFound, DirtyWorktree, UnmergedBranch, TaskNotFound, ActiveTaskConflict, LockedWorktree, InvalidTaskName, GitTimeout, InstanceConflict, NotAGitRepo, RollbackIncomplete, PortUnavailable, Internal`. Codes are contract; messages may change.
- **All JSON keys are `snake_case`** (tool I/O AND `/state`) — no camelCase, no translation layer; dashboard JS reads snake_case directly.
- **Timestamps via the single `now_iso()` helper:** UTC ISO-8601 with `Z`, second precision (`2026-06-22T11:00:00Z`). Never `datetime.now()` (local) or epoch ints.
- **Pydantic `*In` models live only at the tool boundary** (`tools/models.py`); core functions take plain typed args, not the Pydantic model.

### Persistence & derive-on-read
- **Derive-on-read; never persist derived state.** `git worktree list --porcelain` **per repo** is the sole truth for worktree existence; SQLite stores only task records + per-repo `(repo_path, branch, worktree_path)` links; the view is recomputed into an ephemeral in-memory cache.
- **`/state` reads the in-memory cache only — never shells out to git on a poll.** `projection.py` is a pure `(git_listing, annotations) → view` function: no writes, no destructive git. The snapshot is immutable and swapped whole.
- **`store.py` is the only module that opens SQLite; `git/runner.py` the only one that spawns git.** WAL + `busy_timeout` + `foreign_keys=ON` at bootstrap; two tables (`task`, `task_worktree`); migrations = version-check only (`PRAGMA user_version`); re-tasking a slug is an UPSERT.
- **Runtime state lives in XDG, never in the repo/package:** `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/{state.db, server.lock}`. Worktrees are siblings of each repo (`<repo>.worktrees/<task>/`). Nothing runtime under `src/`.

### Security
- **Bind `127.0.0.1` only — never `0.0.0.0`** (asserted by the uvicorn smoke test).
- **Origin middleware is our own, outermost,** over `/mcp` AND dashboard routes: present + non-allowlisted Origin → **403**; **absent Origin → allow** (non-browser MCP clients send none); allowlisted → allow.
- **Bake the bound port into the Origin allowlist** — scan for the free port first, then build the app/middleware with `{http://127.0.0.1:<port>, http://localhost:<port>}`. Never hardcode 8765 in the allowlist.
- **`<task>` slug is an injection boundary:** validate against the pinned regex (lowercase, hyphenate, collapse, max 60, reject empty/reserved/`.`/`..`) before any shell-out; git via `exec` with `--` end-of-options. Collision in **any** requested repo → structured reject (no silent suffixing); `create_task` is all-or-nothing across repos.

### Naming & structure
- `snake_case` functions/vars/modules; `PascalCase` classes; `UPPER_SNAKE` module constants; type hints on every public signature; `_`-prefixed privates; module names are nouns (`store.py`, `cache.py`, `lock.py`, `errors.py`).
- **All tunables in `config.py`** — no magic numbers scattered in modules.
- `tests/` mirrors `src/` module layout; files named `test_<module>.py`.
- `logging.getLogger(__name__)` per module; level from `DEV_HELPER_LOG` (default `INFO`); to stderr; never log full annotation contents at `INFO`.

### Testing rules (incl. harness gotchas proven in Story 1.1)
- Prefer the **in-process `httpx.ASGITransport`** harness for HTTP/tool/Origin tests; temp/`:memory:` DB for Store tests; **exactly one** real-port uvicorn smoke test asserting `127.0.0.1` bind, **`@pytest.mark.slow`**-marked so the gate runs the fast suite by default.
- **`ASGITransport` does NOT auto-run the lifespan** — wrap test bodies in `async with app.router.lifespan_context(app):` or hit `"Task group is not initialized"`.
- **No `pytest-asyncio`** — drive async tests with `asyncio.run()` inside sync test functions.
- **In-process base URL must be `http://127.0.0.1:<port>`** (not httpx's default `testserver`), or FastMCP's own host validation returns **421 Misdirected Request**.
- A FastMCP tool returning a plain `dict` serializes as **JSON text content** (`content[0].text`); `structuredContent` stays `None` — assert by parsing the text.

### Code-quality gate & workflow
- **Enforced pre-commit gate** (no CI in v1): `ruff check` + `ruff format --check` + `pytest -m "not slow"`. Hook at **`.githooks/pre-commit`**, wired via **`git config core.hooksPath .githooks`** (survives the `agent/<task>` worktrees this tool creates, which share one `.git`). Any failure blocks the commit.
- **Ruff is scoped to our code:** `extend-exclude = [".claude", "_bmad", "_bmad-output", "docs"]` so vendored BMad/skill scripts don't fail the gate. Run gate-equivalent locally with the hook or `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`.
- **Run/dist:** `uv run dev-helper-mcp` (**no `--repo`** — the server is global; learns repos from `create_task`). Console entry `dev-helper-mcp = dev_helper_mcp.cli:main`; also `python -m dev_helper_mcp`. Distributed via `uv tool install`.
- Pattern changes are made in `architecture.md` first, then propagated — not invented ad-hoc in a story.

### Anti-patterns — reject in review
- `subprocess.run(["git", …])` directly (bypasses pool/timeout/env).
- A tool returning a bare success dict but raising on failure (non-uniform shape).
- `datetime.now()` / epoch ints in JSON; `camelCase` keys; any case-translation layer.
- Core logic importing `from mcp …` / `from starlette …`.
- Persisting derived state, or shelling out to git on the `/state` poll path.
- Hardcoding `8765` in the Origin allowlist; binding `0.0.0.0`.
- A destructive git op (`worktree remove --force`, `branch -D`) on the read/refresh path.
