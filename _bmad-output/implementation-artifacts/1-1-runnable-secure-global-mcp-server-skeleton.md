# Story 1.1: Runnable, secure global MCP server skeleton

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want to start a single global `dev-helper-mcp` server that an MCP client can connect to over Streamable HTTP on localhost,
so that the transport, mount, and Origin-security foundation is proven end-to-end before any real tools exist.

## Acceptance Criteria

1. **Scaffold + runnable server (AR-1, AR-3, FR-13 bootstrap).**
   **Given** a clean checkout scaffolded with `uv init --package` (`src/` layout, `pyproject.toml`, pins `mcp>=1.28,<2` + `aiosqlite`, `.python-version` 3.12, `.gitignore`),
   **When** I run `uv run dev-helper-mcp`,
   **Then** the server binds `127.0.0.1` on the first free port in 8765→8775 and prints the dashboard URL on startup.

2. **MCP handshake + no-op tool, no 307 (AR-3, FR-11 seed).**
   **Given** the running server,
   **When** an MCP client connects to `http://127.0.0.1:<port>/mcp` and lists tools,
   **Then** the MCP handshake completes with **no 307 redirect** (`streamable_http_path="/"` + `Mount("/mcp")`, lifespan wrapped) and a trivial registered health/ping tool round-trips.

3. **Origin-validation middleware (AR-4, NFR-Security).**
   **Given** a request carrying a non-allowlisted `Origin` header,
   **When** it hits `/mcp` or any route,
   **Then** the outermost Origin-validation middleware returns `403`;
   **And** a request with an **absent** `Origin` (non-browser MCP client) is allowed.

4. **127.0.0.1 bind + adapter-seam (NFR-Security, AR-2).**
   **Given** the bound server,
   **When** the uvicorn smoke test inspects the bind address,
   **Then** it is `127.0.0.1`, **never `0.0.0.0`**;
   **And** no module under `core/`, `git/`, `store`, `projection`, `cache` imports `mcp`/`starlette` (adapter-seam test).

5. **Enforced pre-commit quality gate (AR-12) — established here, NOT deferred to Epic 3.**
   **Given** the scaffold with dev dependencies `ruff` + `pytest` and `tests/` mirroring `src/`,
   **When** the enforced pre-commit hook is installed as part of this first story,
   **Then** committing runs `ruff check`, `ruff format --check`, and `pytest` and **blocks the commit on any failure** — establishing the regression gate (no CI in v1) so it guards **every subsequent story** from the start;
   **And** the no-op tool round-trip plus the bind and adapter-seam tests above run green under this gate.

## Tasks / Subtasks

- [ ] **Task 1 — uv scaffold + dependency pins + entry point (AC: 1, 5)**
  - [ ] `uv init --package dev-helper-mcp` producing `src/dev_helper_mcp/` layout, `pyproject.toml`, `uv.lock`, `uv_build` backend
  - [ ] `uv add "mcp>=1.28,<2"` and `uv add "aiosqlite"` (Starlette/uvicorn arrive transitively via `mcp`) — **do not** add Starlette/uvicorn/FastMCP-standalone as direct deps
  - [ ] `uv add --dev ruff pytest` (dev group); add `httpx` to the test/dev deps for the in-process `ASGITransport` harness
  - [ ] Pin `.python-version` to `3.12`; set `requires-python = ">=3.10"` in `pyproject.toml`
  - [ ] `.gitignore` includes the venv, `__pycache__`, build artifacts, and the worktree sibling pattern `*.worktrees/` (runtime state lives in XDG, not the repo — no `.dev-helper-mcp/` in-repo)
  - [ ] Declare the console entry point `dev-helper-mcp = "dev_helper_mcp.cli:main"` and `python -m dev_helper_mcp` via `__main__.py`
  - [ ] Add `[tool.ruff]` config (line length, target-version py312) and a `[tool.pytest.ini_options]` block registering the `slow` marker
- [ ] **Task 2 — config + constants module (AC: 1, 3)**
  - [ ] `config.py`: `DEFAULT_PORT = 8765`, `PORT_RANGE = range(8765, 8776)` (8765→8775 inclusive), allowed-origin **host** set `{127.0.0.1, localhost}`, app name string — single source, no magic numbers elsewhere
- [ ] **Task 3 — Origin-validation middleware (AC: 3) — our own, NOT FastMCP's**
  - [ ] `middleware.py`: `OriginValidationMiddleware` (pure Starlette `BaseHTTPMiddleware` or ASGI middleware). Rule: `Origin` **present and not** in `{http://127.0.0.1:<port>, http://localhost:<port>}` → `403`; `Origin` **absent** → allow; allowlisted → allow
  - [ ] The bound port MUST be passed into the middleware/app factory so the allowlist is exact (see Dev Note "Port↔Origin chicken-and-egg"); never hardcode 8765 in the allowlist
- [ ] **Task 4 — SDK adapter: server_factory (AC: 2, 3, 4)**
  - [ ] `server_factory.py` (the ONLY module besides `server`/`middleware`/`cli` that imports `mcp`/`starlette` in this story): build `FastMCP("dev-helper-mcp")`, register one no-op `ping` tool returning a trivial `{ok: true, ...}`-shaped value
  - [ ] Set `mcp.settings.streamable_http_path = "/"`; build `mcp_app = mcp.streamable_http_app()`
  - [ ] Build the app-owned Starlette app: `Mount("/mcp", app=mcp_app)` + the Origin middleware as the **outermost** parent middleware; lifespan MUST wrap `async with mcp_app.lifespan(mcp_app):` (load-bearing — otherwise `/mcp` fails "Task group is not initialized")
- [ ] **Task 5 — server lifecycle + CLI (AC: 1)**
  - [ ] `server.py`: scan `PORT_RANGE` for the first free port on `127.0.0.1`, build the app with that port, run uvicorn bound to `127.0.0.1` (never `0.0.0.0`), print the dashboard URL (`http://127.0.0.1:<port>/`) on startup
  - [ ] `cli.py` `main()`: minimal arg parsing (accept `--port N` optional override is acceptable but full strict-override/`stop` semantics are Story 3.2 — keep minimal here), dispatch to `server`. **No `--repo` flag** (the server is global)
  - [ ] `__main__.py`: `from .cli import main; main()`
- [ ] **Task 6 — core-layer seam anchor (AC: 4)**
  - [ ] Create empty core-layer packages `core/__init__.py`, `git/__init__.py` and `util.py` with `now_iso()` (UTC ISO-8601 `Z`, second precision) — anchors the adapter seam so the seam test has real modules to scan and `now_iso()` exists for later stories. Do **not** add `mcp`/`starlette` imports here
- [ ] **Task 7 — tests (`tests/` mirrors `src/`) (AC: 2, 3, 4)**
  - [ ] `conftest.py`: in-process `httpx.ASGITransport` client fixture against the Starlette app
  - [ ] `test_server_factory.py`: mount resolves, lifespan starts the session manager, `/mcp` handshake completes with **no 307**, `ping` tool round-trips
  - [ ] `test_middleware_origin.py`: Origin matrix on `/mcp` AND a non-`/mcp` route — non-allowlisted Origin → 403, absent Origin → allow, allowlisted Origin → allow
  - [ ] `test_smoke_uvicorn.py`: spin a real ephemeral uvicorn, assert the bound socket is `127.0.0.1` not `0.0.0.0`; mark `@pytest.mark.slow`
  - [ ] `test_adapter_seam.py`: walk `core/`, `git/`, and (when they exist) `store`/`projection`/`cache` modules; assert none import `mcp` or `starlette`
- [ ] **Task 8 — install + verify the enforced quality gate (AC: 5)**
  - [ ] Add a tracked hook at `.githooks/pre-commit` running `ruff check`, `ruff format --check`, and `pytest` (fast suite; the `slow` smoke test may be excluded by default), exiting non-zero on any failure
  - [ ] Wire it via `git config core.hooksPath .githooks` (robust across the `agent/<task>` worktrees this tool creates, which share one `.git`) and document the one-time install in `README.md`
  - [ ] Verify: a deliberately failing lint/test blocks `git commit`; a clean tree commits; all Task 7 tests pass under the gate

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
This is the **walking skeleton**. Build ONLY transport + security + scaffold + the quality gate. **Explicitly OUT of scope for this story** (do not pull forward):
- **No real git** — no `run_git()`, no two pools, no porcelain parsing (Story 1.2).
- **No real DB / schema** — `aiosqlite` is *pinned as a dependency* but `store.py` / the two-table schema is **Story 1.2**. Do not create tables here.
- **No real tools** — `create_task`/`list_worktrees`/`remove_worktree`/`update_task`/`list_tasks` are Stories 1.3–1.6. Only a throwaway `ping`/health tool here.
- **No dashboard UI / `/state`** — Epic 2. The "dashboard URL" printed at startup may resolve to a placeholder/empty route for now; the real board is 2.4a.
- **No lockfile / single-instance protocol** — Story 3.1. Port *scanning* for the first free port is in scope; the lockfile, stale-reclaim, and `stop` are not.

The goal is end-to-end proof of the highest-risk wiring (Streamable HTTP mount, lifespan propagation, Origin security, 127.0.0.1 bind) **plus** the regression gate that protects every later story.

### Binding invariants this story establishes (from architecture.md § Invariants)
- **Invariant 7 — SDK-isolation seam:** core logic imports no `mcp`/`starlette`. Only the adapter layer (`server_factory`, `server`, `middleware`, `cli` — and later `tools/`, `dashboard/`, `lock`) touches the SDK. This is the v2-migration seam; AC 4's seam test enforces it. [Source: architecture.md#Invariants; #Architectural Boundaries]
- **Invariant 8 — MCP mount wiring is REQUIRED and load-bearing:** `streamable_http_path="/"` + `Mount("/mcp", …)` (no 307); the app-owned lifespan MUST wrap `async with mcp_app.lifespan(mcp_app)`. Skipping the lifespan wrap makes every `/mcp` request fail with "Task group is not initialized." [Source: architecture.md#Invariants; #Critical Wiring Notes]
- **Invariant 9 — Origin middleware REQUIRED, outermost, over `/mcp` AND dashboard routes; bind `127.0.0.1` only, never `0.0.0.0`.** [Source: architecture.md#Invariants; #Authentication & Security]

### Critical wiring details (verified during Party Mode — carry into implementation)
- **307-redirect fix (python-sdk #1168):** set `mcp.settings.streamable_http_path = "/"` **and** `Mount("/mcp", app=mcp.streamable_http_app())`. Clients connect to `http://127.0.0.1:<port>/mcp` with no trailing-slash redirect. [Source: architecture.md#Critical Wiring Notes]
- **Lifespan propagation:** Starlette does NOT auto-run a mounted sub-app's lifespan. The app-owned lifespan must `async with mcp_app.lifespan(mcp_app):` or the StreamableHTTP session manager never starts. [Source: architecture.md#Critical Wiring Notes]
- **Use OUR Origin middleware, not FastMCP's `TransportSecurityMiddleware`** — the mounted-sub-app layout bypasses FastMCP's own security middleware, so it must live on the parent Starlette app as the outermost layer. [Source: architecture.md#Critical Wiring Notes; #Authentication & Security]
- **Port↔Origin chicken-and-egg (gotcha):** the Origin allowlist is `{http://127.0.0.1:<port>, http://localhost:<port>}`, but the port is only known after scanning for a free one. Flow: scan → know port → build the app/middleware with that port baked into the allowlist → serve. Never hardcode 8765 in the allowlist. (Alternatively validate the host portion only — but match the architecture's explicit allowlist form.) [Source: architecture.md#Authentication & Security]
- **Origin semantics:** present + non-allowlisted → `403`; **absent → allow** (Claude Code and other non-browser MCP clients send no `Origin`). This must apply to all routes including `/mcp`. [Source: architecture.md#Authentication & Security]

### Conventions to follow (architecture.md § Implementation Patterns)
- `snake_case` for functions/variables/modules, `PascalCase` for classes, `UPPER_SNAKE` for module constants; type hints on every public signature; module names are nouns. [Source: architecture.md#Naming Patterns]
- All tunables live in `config.py` — no magic numbers scattered in modules. [Source: architecture.md#File Organization Patterns]
- `now_iso()` is the single timestamp helper: UTC ISO-8601 with `Z`, second precision (e.g. `2026-06-19T13:18:25Z`). Never `datetime.now()` (local) or epoch ints. [Source: architecture.md#Data & Format Patterns]
- The `{ok, data, error}` envelope is the tool contract; the `ping` tool should return an `ok`-shaped result to seed the convention, even though the full typed `DevHelperError`→envelope path is Story 1.2+. [Source: architecture.md#Result Envelope & Error Patterns]
- `logging.getLogger(__name__)` per module; level from `DEV_HELPER_LOG` (default `INFO`); log to stderr. [Source: architecture.md#Structure & Process Patterns]

### Source tree components to touch (this story only)
From the architecture's complete tree, this story creates:
```
dev-helper-mcp/
├── pyproject.toml        # deps (mcp>=1.28,<2; aiosqlite), dev (ruff, pytest, httpx), entry point, ruff/pytest config
├── uv.lock
├── .python-version       # 3.12
├── .gitignore
├── README.md             # run + one-time hook install instructions
├── .githooks/pre-commit  # ruff check + ruff format --check + pytest (the enforced gate)
├── src/dev_helper_mcp/
│   ├── __init__.py
│   ├── __main__.py       # python -m dev_helper_mcp → cli.main()
│   ├── cli.py            # minimal arg parsing (NO --repo); dispatch to server
│   ├── config.py         # DEFAULT_PORT, PORT_RANGE, origin host allowlist
│   ├── server_factory.py # FastMCP + ping tool + streamable_http_path="/" + Origin mw (outermost) + Mount("/mcp") + lifespan wrap
│   ├── server.py         # port auto-fallback bind, run uvicorn on 127.0.0.1, print URL
│   ├── middleware.py     # OriginValidationMiddleware
│   ├── util.py           # now_iso()
│   ├── core/__init__.py  # seam anchor (no mcp/starlette)
│   └── git/__init__.py   # seam anchor (no mcp/starlette)
└── tests/
    ├── conftest.py
    ├── test_server_factory.py
    ├── test_middleware_origin.py
    ├── test_smoke_uvicorn.py   # @pytest.mark.slow
    └── test_adapter_seam.py
```
Modules **deferred** to later stories (do not create them yet): `errors.py`, `tools/`, `dashboard/`, `lock.py`, `store.py`, `projection.py`, `cache.py`, `git/runner.py`, `git/porcelain.py`, `core/worktrees.py`, `core/tasks.py`, `core/slug.py`. [Source: architecture.md#Complete Project Directory Structure]

### Testing standards
- `tests/` mirrors `src/` module layout; files named `test_<module>.py`. [Source: architecture.md#Structure & Process Patterns]
- Prefer the **in-process `httpx.ASGITransport`** harness for HTTP/tool/Origin tests (no port). [Source: architecture.md#Test harness baseline]
- Exactly **one** real-port uvicorn smoke test asserting the bind is `127.0.0.1`, not `0.0.0.0`; `slow`-mark it so the pre-commit gate can run the fast suite by default. [Source: architecture.md#Test harness baseline; #CI/CD]
- The quality gate is `ruff check` + `ruff format --check` + `pytest`, **enforced via the pre-commit hook** (enforcement, not "local discipline", replaces CI's regression gate). The gate is extensible — Epic 2 adds a `node --test` for the dashboard poller `diff()`; do not add it now. [Source: architecture.md#CI/CD; epics.md#AR-12]

### Project Structure Notes
- **Runtime state is NOT in the repo or the package:** the machine-global DB + lockfile live at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/` and are created at runtime in later stories — never under `src/`. This story creates no runtime state. [Source: architecture.md#File Organization Patterns; #Runtime state]
- The `src/` layout (not flat) is deliberate: it prevents accidental dev-code imports and runs tests against the installed package. [Source: architecture.md#Starter Options Considered]
- No conflicts with the unified structure detected — this story is a strict subset of the architecture's defined tree.

### Latest tech / version notes
- **`mcp` SDK pinned `>=1.28,<2`** deliberately: official SDK **v2 (stable 2026-07-27) is a known breaking event**. The `<2` upper bound fences it; a v2 migration is a tracked future decision contained entirely to the adapter seam. Use `mcp.server.fastmcp.FastMCP` and `streamable_http_app()` from the 1.28 line. [Source: architecture.md#Decisions at a Glance; #Version risk captured]
- **`aiosqlite`** is added now (pinned dependency) but unused until Story 1.2 — it is fine to have it installed; do not bootstrap a DB in this story.
- Python **≥3.10, target 3.12** (`.python-version` = 3.12). [Source: architecture.md#Architectural Decisions Provided by This Foundation]
- Minimal dependency posture (NFR-Simplicity): only `mcp` (+ transitive Starlette/uvicorn) and `aiosqlite` as runtime deps; `ruff`/`pytest`/`httpx` as dev deps. [Source: architecture.md#Dependency posture]

### Git / previous-work intelligence
First story of the project — no previous story to learn from. The repo's git history to date contains only BMad planning-artifact commits (PRD, architecture, epics, install); the `src/` codebase is greenfield. No prior code patterns to match beyond the architecture's conventions above.

### References
- [Source: epics.md#Story 1.1: Runnable, secure global MCP server skeleton] — acceptance criteria
- [Source: epics.md#AR-1] — uv scaffold; [Source: epics.md#AR-2] — SDK adapter seam; [Source: epics.md#AR-3] — MCP mount wiring; [Source: epics.md#AR-4] — Origin middleware; [Source: epics.md#AR-12] — quality gate
- [Source: epics.md#Epic 1] — story-decomposition guidance (foundation-first; 1.1 = walking skeleton, no git/DB)
- [Source: architecture.md#Invariants] — invariants 7, 8, 9
- [Source: architecture.md#Starter Template Evaluation] — scaffold + framework selection, init commands
- [Source: architecture.md#Critical Wiring Notes] — 307 fix, lifespan propagation, our-own Origin middleware
- [Source: architecture.md#Authentication & Security] — Origin allowlist + 127.0.0.1 bind
- [Source: architecture.md#Complete Project Directory Structure] — module homes and test files
- [Source: architecture.md#CI/CD] — enforced pre-commit quality gate

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
