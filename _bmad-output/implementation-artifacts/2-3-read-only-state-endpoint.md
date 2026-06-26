---
baseline_commit: f25615be6e7e2b051ddcef54550d799bbece950a
---

# Story 2.3: Read-only `/state` endpoint

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard client,
I want a `/state` JSON endpoint served from the in-memory cache,
so that I can poll current state cheaply and safely without triggering any git work or mutation.

## Acceptance Criteria

1. **Given** the in-memory cache,
   **When** `GET /state` is called,
   **Then** it returns the current `CacheSnapshot` as snake_case JSON including `generated_at`, read from the cache **by reference only** — no git shell-out on the poll path.

2. **Given** any dashboard route including `/state`,
   **When** a request carries a non-allowlisted `Origin`,
   **Then** it is rejected `403` by the **same outermost middleware** as `/mcp`;
   **And** an absent `Origin` is allowed.

3. **Given** the dashboard's served interface,
   **When** it is inspected,
   **Then** it exposes **no mutating route or action** (no create/modify/remove of worktrees or tasks, no agent launch) — the read-only guarantee is asserted by test.

## ⛔ HARD PREREQUISITE — read before anything else

**Story 2.3 cannot be implemented until Stories 2.1 and 2.2 are implemented.** This story only *serves* the in-memory cache that 2.2 builds over the projection 2.1 defines. It adds **zero** projection/cache logic and changes neither `projection.py` nor `cache.py`.

- 2.1 (`ready-for-dev`) ships `src/dev_helper_mcp/projection.py`: the frozen `CacheSnapshot`/`TaskView`/`WorktreeView` dataclasses (snake_case fields = the `/state` JSON contract) + the pure `project()`.
- 2.2 (`ready-for-dev`) ships `src/dev_helper_mcp/cache.py`: the `Cache` class with `@property current -> CacheSnapshot` (by-ref, no lock, GIL-atomic swap), the background refresher, and the lifespan wiring that builds the `Cache`, **warms it** (`await cache.refresh()` before serving), and stores it on `ToolDeps.cache` (exposed via `_DepsHolder`).
- If `cache.py`/`projection.py` do not exist when you start, implement 2.1 then 2.2 first, get their gates green, then return here.
- Treat the `CacheSnapshot` shape and `Cache.current` as a **fixed contract** — 2.3 reads them, never edits them.

## Tasks / Subtasks

- [x] **Task 1 — Create the `dashboard/` adapter package** (AC: 1)
  - [x] `src/dev_helper_mcp/dashboard/__init__.py` (empty package marker). This is the architecture's `dashboard/` package (adapter layer; FR-8–10, architecture.md:768, 793, 826). Story 2.4a/b/c add the HTML board + JS to this same package; 2.3 adds **only** `/state`.
  - [x] `src/dev_helper_mcp/dashboard/routes.py` (NEW, adapter — MAY import `starlette`). It is the **only** new file that touches the SDK in this story. It is NOT in `tests/test_adapter_seam.py`'s `SEAM_MODULES` (that scan covers `core/`, `git/`, `store`, `projection`, `cache` — `dashboard/` is adapter, allowed SDK), so importing `starlette` here is correct, not a violation.
- [x] **Task 2 — Serialize the snapshot (snake_case, pure)** (AC: 1)
  - [x] `dataclasses.asdict(snapshot)` recurses the frozen `CacheSnapshot` → nested snake_case `dict` (tuples → lists under `json.dumps`). The field names ARE the contract (Invariant 3) — **no rename, no camelCase, no translation layer** (project-context.md:49). Do NOT hand-roll a serializer that could drift from 2.1's shape; `asdict` is the single transform.
  - [x] Keep this a one-liner inside the route handler — there is no separate "serializer" module to build (anti-scope-creep). The dict from `asdict` is handed straight to `JSONResponse`.
- [x] **Task 3 — Implement the `/state` GET handler** (AC: 1, 3)
  - [x] In `dashboard/routes.py`, expose a **factory** `def state_route(holder: _DepsHolder) -> Route:` (or `make_state_endpoint(holder) -> callable`) that closes over the same `_DepsHolder` the tool closures capture — that is how the route reaches the loop-bound `Cache` (`holder.deps.cache.current`). Mirror the existing closure-over-holder pattern in `server_factory.build_mcp` (server_factory.py:64-185).
  - [x] Handler: `async def state(request: Request) -> JSONResponse:` — read `deps = holder.deps`; **guard the startup/teardown window**: if `deps is None` (or, defensively, `deps.cache is None`) return `JSONResponse({"detail": "server not ready"}, status_code=503)` (Decision A). Otherwise `snap = deps.cache.current` (by-ref, no await, **no git, no lock**) → `return JSONResponse(dataclasses.asdict(snap))`.
  - [x] **Methods = `["GET"]` only.** A non-GET (`POST`/`PUT`/`DELETE`) to `/state` is rejected without mutation. *Implementation note:* the literal "automatic 405" assumption does not hold while the catch-all `Mount("/")` sits behind the route — Starlette's `Route(methods=["GET"])` returns only a **partial** match for non-GET, and the Mount **fully** matches and short-circuits, so a non-GET falls through to the MCP app and is rejected there (**404**, not 405). No other method was added; the read-only guarantee (no mutating action at `/state`) holds either way, and the AC3-binding criterion ("no mutating route or action") is satisfied. See the route-table assertion (the authoritative read-only proof) and the documented test.
  - [x] **No git, no DB, no `await` on I/O.** The handler does a single in-memory ref read. If you find yourself importing `GitRunner`/`Store`/`run_git` here, stop — that violates "`/state` never shells out on a poll" (Invariant; architecture.md:358, 811-812).
- [x] **Task 4 — Wire the route so it WINS over the catch-all MCP mount** (AC: 1) — *the load-bearing wiring fix*
  - [x] **The problem (deferred-work.md, flagged FOR THIS STORY):** `server_factory.create_app` currently mounts the MCP sub-app at `Mount("/", app=mcp_app)` (server_factory.py:217), which owns the **entire** URL space — any sibling route added naively is unreachable. Epic 2's `/state` must revisit this. (deferred-work.md "Deferred from story-1-1 → `Mount('/', app=mcp_app)` shadows future routes … deferred to Epic 2.")
  - [x] **The fix (route ordering — the lowest-risk of the three options):** Starlette evaluates `routes` in order and returns the first match, so list the explicit `Route("/state", …)` **before** the catch-all `Mount("/", app=mcp_app)`:
    ```python
    routes=[
        state_route(holder),          # explicit, matched first → wins
        Mount("/", app=mcp_app),      # catch-all (keeps /mcp working) → matched second
    ]
    ```
    `/mcp` still resolves through the Mount (it is not `/state`), so AC2-of-1.1 (no 307, handshake) is preserved. Verify the existing `tests/test_server_factory.py` (`/mcp` no-307, lifespan starts session mgr) stays green.
  - [x] `holder` already exists in `create_app` (server_factory.py:194). Pass the **same** `holder` instance into `state_route(holder)` so the route and the tool closures share one deps source. Do not create a second holder.
  - [x] **Do NOT** register `/state` *inside* the MCP sub-app, and do NOT move MCP to a sub-path — both are heavier than ordering and risk re-introducing the 307. Route-ordering is the chosen option; record why in a code comment referencing the deferred-work item.
- [x] **Task 5 — Confirm Origin middleware already covers `/state` (no new code)** (AC: 2)
  - [x] `OriginValidationMiddleware` is the **outermost parent-app middleware** (server_factory.py:218), so it runs on **every** request before route dispatch — `/state` is covered automatically the moment the route exists. **Write no new middleware.** (middleware.py:35-49: present+non-allowlisted Origin → 403; absent → allow; allowlisted → allow.)
  - [x] Add a `/state` row to the Origin matrix test (Task 6) — the *coverage* is free; the *proof* is the new assertion.
- [x] **Task 6 — Tests: `tests/test_dashboard_state.py`** (AC: 1, 2, 3)
  - [x] Use the **in-process `httpx.ASGITransport`** harness (the `asgi_client_factory`/equivalent in conftest.py), base URL `http://127.0.0.1:<port>` (NOT httpx's default `testserver`, or FastMCP host-validation 421s — project-context.md:75), and **wrap the body in `async with app.router.lifespan_context(app):`** (ASGITransport does NOT auto-run the lifespan — project-context.md:73). Drive async via `asyncio.run()` (no `pytest-asyncio`).
  - [x] **AC1 — shape + by-ref + no-git:** `GET /state` → 200; body is JSON with `generated_at` (str) + `tasks` (list) + `warnings` (list); all keys snake_case; the payload equals `dataclasses.asdict(holder.deps.cache.current)` at call time. After a `create_task` against a `tmp_git_repo` (which 2.2's post-mutation refresh updates the cache), `GET /state` reflects the new task — proving it reads the live cache, not a stale constant. **Assert no git is spawned on the GET** (spy on `GitRunner.run_git` — zero calls during the bare `GET /state` — AND the project-repo guard stays green).
  - [x] **AC2 — Origin matrix on `/state`:** `GET /state` with a non-allowlisted `Origin` header → 403; with an **absent** `Origin` → 200/allow; with an allowlisted `Origin` (`http://127.0.0.1:<port>`) → allow. (Added `/state` to the existing `tests/test_middleware_origin.py` matrix `ROUTES`. Same outermost middleware as `/mcp`.)
  - [x] **AC3 — read-only:** non-GET (`POST`/`PUT`/`DELETE`) → rejected without mutation (404 via the catch-all Mount fallthrough — see Task 3 note); the served dashboard interface exposes no mutating route. Assert the route table: the parent app's routes are exactly `[Route("/state", GET), Mount("/")]` — there is no dashboard route that creates/modifies/removes a task or worktree. (The MCP `/mcp` tool surface is the *agent* API, deliberately separate from the *dashboard* interface this AC governs.)
  - [x] **503 startup/teardown window (Decision A):** with `holder.deps` `None` (lifespan not entered), `GET /state` → 503 with a JSON body `{"detail": "server not ready"}` (not a stack trace, not a blank 500). Keeps the poller resilient during the brief deps-null window.
- [x] **Task 7 — Gate green + seam confirmation** (AC: all)
  - [x] `dashboard/routes.py` is adapter (imports `starlette`) and is **not** in `SEAM_MODULES`; `projection.py`/`cache.py` are **unchanged**, so `tests/test_adapter_seam.py` stays green (verified passing).
  - [x] Full gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` — **209 passed, 5 deselected (slow); ruff check + format clean.** **No new dependency** (stdlib `dataclasses` + already-present `starlette`/`httpx`). No schema change, no migration, no new git command, no HTML/JS (that is 2.4a+). Gate run manually (pre-commit test enforcement is intentionally off).

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
2.3 is the **third Epic 2 story**: it exposes the 2.2 cache over HTTP as a read-only JSON endpoint and nothing more. It is the thin seam between the in-memory cache and the dashboard UI.

- **BUILD:** `src/dev_helper_mcp/dashboard/{__init__.py, routes.py}` (the `/state` GET handler + a `state_route(holder)` factory), the route-ordering fix in `server_factory.create_app`, and `tests/test_dashboard_state.py`.
- **DO NOT BUILD (later stories — hard fence):**
  - **No HTML, no CSS, no JS, no board, no `/` index route** → **Story 2.4a** (and the poller is 2.4b, the edge-state UI 2.4c). 2.3 ships JSON only; a browser hitting `/state` sees raw JSON, and that is correct for this story.
  - **No change to `cache.py`/`projection.py`** — do not add a serializer to the cache, do not add fields to the snapshot, do not add a `Cache.to_json()`. `dataclasses.asdict` at the route boundary is the whole transform.
  - **No new middleware** — Origin enforcement already covers `/state` (it is parent-outermost). Adding a route-specific guard would duplicate the chokepoint.
  - **No git, no DB, no `await` I/O on the poll path** — a single in-memory ref read. This is the entire point of the derive-on-read cache (Invariant 4; architecture.md:358).
  - **No freshness/stale logic, no degrade rendering** — `/state` returns the snapshot *as is* (including 2.2's `repo_unavailable:`/`orphan_link:` warnings and the aging `generated_at`); interpreting them into UI states is **Story 2.4c**. 2.3 is a faithful JSON mirror.
- [Source: epics.md:381-401 (this story); epics.md:404-474 (2.4a/b/c own the UI); architecture.md:741-742, 768-781, 793; deferred-work.md]

### ✅ Decision A — deps-null window behavior (OPERATOR-CONFIRMED 2026-06-25: 503)
`holder.deps` is `None` only during the lifespan startup/teardown window (before `Store.open()` / after teardown nulls it — server_factory.py:204, 213). `Cache.current` itself always returns a snapshot (2.2 seeds it to an empty `CacheSnapshot` before the first tick), so the *only* null is `holder.deps`.
- **DECIDED: `503 Service Unavailable`** with a tiny JSON body (`{"detail":"server not ready"}`). Honest ("not ready yet"), brief (the window is milliseconds), and the 2.4b poller simply retries on its next tick. Mirrors the tools' "server not ready" envelope guard (server_factory.py:91-92) in spirit, in the HTTP idiom. The body is JSON, never a leaked stack trace.
- *Rejected:* `200` with an empty snapshot (`{"generated_at": now_iso(), "tasks": [], "warnings": []}`) — it manufactures a second `now_iso()` site outside the cache and reports "0 tasks" as if true during startup (a small lie); the never-blank guarantee is properly a **2.4c** stale-labeling concern, not something to fake at `/state`.

### The route-shadowing fix is the heart of this story
The single non-obvious task is Task 4. The `Mount("/", app=mcp_app)` from Story 1.1 was the mandated 307-fix (a bare `/mcp` resolves with a clean 200 only when MCP is served at `streamable_http_path="/mcp"` mounted at `/` — server_factory.py:14-22, module docstring). That mount owns `/`, so a naively-appended `/state` route is shadowed. **Starlette matches `routes` top-to-bottom and returns the first match**, so placing `Route("/state")` *before* the `Mount("/")` makes `/state` win while every other path (including `/mcp`) falls through to the Mount. This is the cheapest of deferred-work.md's three suggested options (register-inside-MCP / mount-at-subpath / order-routes) and touches the least surface. **Confirm `/mcp` still 200s with no 307** (existing `test_server_factory.py`) after the reorder — that is the regression to watch. The same ordering trick will let 2.4a add `Route("/")` for the board (also before the Mount).

### What the substrate already gives you (verified shipped 1.1–1.6; consumed contract from 2.1/2.2)
- **`server_factory.create_app(port)`** (server_factory.py:188-220) — builds the parent Starlette app; owns `holder`, the lifespan, the `Mount("/")`, and the Origin middleware. 2.3 edits the `routes=[…]` list (add `state_route(holder)` first) — nothing else here changes shape.
- **`_DepsHolder`** (server_factory.py:53-61) — the mutable holder the tool closures read at call time; `holder.deps` is populated inside the lifespan and nulled on teardown. **The `/state` route reads the cache the same way** (`holder.deps.cache.current`). 2.2 adds `cache` to `ToolDeps`; this story consumes it.
- **`OriginValidationMiddleware`** (middleware.py:35-49) — parent-outermost; already guards every route incl. `/mcp`. `/state` inherits it for free. No edit.
- **`CacheSnapshot` / `Cache.current`** (2.1 `projection.py` / 2.2 `cache.py`) — frozen snapshot + by-ref property. `dataclasses.asdict(current)` is the JSON. Do not re-shape.
- **In-process ASGI harness + lifespan-context + tmp-repo fixtures** (conftest.py) — `asgi_client_factory`, `tmp_git_repo`, `_guard_project_repo_untouched`, `_isolate_state_dir`. Base URL `http://127.0.0.1:<port>`; wrap in `async with app.router.lifespan_context(app):`. (project-context.md#Testing rules.)
- **Existing tests to extend/keep green:** `tests/test_server_factory.py` (mount/lifespan/no-307), `tests/test_middleware_origin.py` (Origin matrix — add `/state`), `tests/test_handlers.py` (envelope shape — untouched).

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 4 — Derive-on-read; `/state` reads the cache ONLY, never shells out to git on a poll.** The single in-memory ref read IS the design. [architecture.md:68, 358, 811-812; project-context.md:55]
- **Invariant 7 — SDK seam:** `/state` lives in `dashboard/` (adapter, MAY import `starlette`); `projection.py`/`cache.py` stay SDK-free and unchanged. Do not import `mcp`/`starlette` into core. [architecture.md:71; project-context.md#SDK-isolation seam]
- **Invariant 3 — snake_case keys** for the `/state` payload — guaranteed by `dataclasses.asdict` over 2.1's snake_case fields; no translation layer. [architecture.md:67; project-context.md:49]
- **Invariant 4/security — Origin on `/mcp` AND dashboard routes** via the same outermost middleware; absent Origin allowed (non-browser clients). [architecture.md:412-414; project-context.md#Security]
- **Read-only dashboard (FR-10)** — the served dashboard interface mutates nothing; `/state` is GET-only (405 on other methods). [epics.md:399-400; architecture.md:775-777]

### Critical gotchas (carry into implementation)
- **⚠️ The test suite is NOT auto-run on commit (intentional — do not restore it).** The pre-commit `pytest` enforcement was **purposefully removed for now** (operator decision 2026-06-25); the hook runs only `ruff`. The quality gate is a **manual command** in v1. So **run `uv run pytest -m "not slow"` and `uv run ruff format --check .` yourself** before considering the story done — do not re-add the pytest line to `.githooks/pre-commit`.
- **Route ORDER matters; the Mount is a catch-all.** `Route("/state")` MUST precede `Mount("/", app=mcp_app)`. Reverse them and `/state` 404s (or worse, the MCP app handles it). Add a one-line comment so a future edit doesn't "tidy" the order and silently break it.
- **`dataclasses.asdict` recurses tuples → JSON arrays.** `tasks`/`worktrees`/`warnings` are tuples in the frozen snapshot; `asdict` keeps them as tuples and `JSONResponse`/`json.dumps` emits arrays. Correct — no manual list conversion needed.
- **By-ref read, never a copy and never a lock.** `deps.cache.current` returns the immutable snapshot by reference; a concurrent swap in `cache.refresh()` just rebinds the ref — the in-flight `asdict` sees a consistent frozen object (no torn read). Do not add a lock; do not deep-copy.
- **Guard `holder.deps is None`.** Without it, the startup/teardown window dereferences `None.cache` → an opaque `AttributeError`/500. Return 503 (Decision A) instead.
- **`/state` is JSON, not HTML, in this story.** Hitting it in a browser shows raw JSON — expected. The board at `/` is 2.4a. Don't add `Content-Type: text/html` or any markup.
- **Don't double-serve.** Only ONE `/state` route; do not also register it inside the MCP app (that path is shadowed and would never be hit anyway, but it's confusing).

### 🛑 Git safety in tests — HARD RULE
2.3's tests exercise `/state` over the in-process ASGI app. The bare `GET /state` path spawns **no git** (that is the AC1 guarantee). The one place git *can* run is a setup `create_task` used to prove the cache reflects a mutation — that MUST target a `tmp_git_repo`, never the project repo. The autouse `_guard_project_repo_untouched` (conftest.py) + `test_git_safety.py` AST scan enforce it. Prefer seeding the cache via 2.2's machinery against a tmp repo; never invoke a tool against a real-world path. (project-context.md#Git safety in tests.)

### Previous-story (2.1 / 2.2 / Epic 1) intelligence that applies directly
- **2.2 already does the post-mutation refresh** in the three mutating handlers, so by the time `/state` is polled after a `create_task`, the cache is current — 2.3 needs no refresh call of its own (and must not add one; `/state` never triggers work).
- **"This file wins over architecture pseudo-code."** The architecture's data-flow sketch (architecture.md:811-812) says `browser → GET /state → dashboard/routes → reads cache → JSON`; the route-shadowing reality (the `Mount("/")` 307-fix) is the *unobvious* part the pseudo-code omits — handle it via route ordering (deferred-work.md). [project-context.md#Usage Guidelines]
- **Test style proven 1.1–2.2:** plain `pytest`, async via `asyncio.run()`, in-process `httpx.ASGITransport` wrapped in the lifespan context, base URL `http://127.0.0.1:<port>`, no new dep, no `slow` test needed here. [project-context.md#Testing rules]
- **Envelope vs payload:** the 5 MCP tools return the `{ok,data,error}` envelope; `/state` is **not** a tool — it returns the **raw snapshot JSON** (no envelope). Don't wrap the snapshot in `{ok,...}`; `/state` is an HTTP endpoint, not an MCP tool. [architecture.md:775-777]

### Git / recent-work intelligence
- **Baseline `cc6c8fe` ("1-6 complete").** Epic 1 fully implemented + reviewed-`done`; the final 5-tool surface is live. Epic 2 prior artifacts: 2.1 + 2.2 drafted `ready-for-dev` (`projection.py`/`cache.py` land with them). `dashboard/` does NOT exist yet — this story creates it.
- **Commit cadence:** one commit per story after a green gate + adversarial code-review. Files touched: NEW `src/dev_helper_mcp/dashboard/__init__.py`, `dashboard/routes.py`, `tests/test_dashboard_state.py`; UPDATE `server_factory.py` (route list — one line, plus a comment). No `core/`/`store.py`/`projection.py`/`cache.py`/`errors.py` change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`, `src/` layout; `from __future__ import annotations` at the top (matches the codebase). Type hints on every public signature.
- **`starlette.responses.JSONResponse`** + `starlette.routing.Route` + `starlette.requests.Request` — already available transitively via `mcp` (project-context.md:22; do NOT add starlette as a direct dep). `JSONResponse` uses `json.dumps` (default separators) — fine for the snapshot.
- **No new runtime dependency.** stdlib `dataclasses` + the already-present `starlette`. No `mcp`/git/DB import in `dashboard/routes.py` beyond `starlette` + `dataclasses` + the holder type.

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/dashboard/__init__.py`, `src/dev_helper_mcp/dashboard/routes.py` (the `/state` handler + `state_route(holder)` factory). `tests/test_dashboard_state.py`.
- **UPDATE:** `src/dev_helper_mcp/server_factory.py` — add `state_route(holder)` to `routes=[…]` **before** `Mount("/")`; import the factory from `.dashboard.routes`. (One functional line + an import + a comment.)
- **UNCHANGED (do not edit):** `projection.py`, `cache.py` (frozen contracts), `store.py`, all of `core/`, `git/`, `errors.py`, `util.py`, `middleware.py` (already covers `/state`), `cli.py`, `server.py`, `tools/` (handlers/models). **DB schema unchanged — no migration.**
- **DEFERRED, do NOT create or pull forward:** the board HTML/CSS + `/` index route (2.4a); the vanilla-JS poller + diff/patch (2.4b); freshness/stale/degraded/empty-state rendering + orphan disclosure (2.4c); any `dashboard/static/` assets (2.4a+). [epics.md:404-474; architecture.md:826]
- Test mirrors src under the `dashboard/` package: `tests/test_dashboard_state.py`. (Architecture planned `tests/test_middleware_origin.py # Origin matrix on /mcp AND /state` — extend that file for the Origin row, and add `test_dashboard_state.py` for the shape/read-only/503 coverage. architecture.md:748.)

### Testing standards
- `tests/test_dashboard_state.py`: plain `pytest`, async via `asyncio.run()`, in-process `httpx.ASGITransport` (base URL `http://127.0.0.1:<port>`), wrapped in `async with app.router.lifespan_context(app):`. Extend `tests/test_middleware_origin.py` with a `/state` Origin row.
- **Coverage to the three ACs:** (1) `GET /state` → 200, snake_case `generated_at`/`tasks`/`warnings`, equals `asdict(cache.current)`, reflects a post-`create_task` mutation, spawns no git; (2) Origin matrix on `/state` (403 non-allowlisted, allow absent, allow allowlisted) — same middleware as `/mcp`; (3) read-only — `POST /state` → 405, the route table exposes no mutating dashboard route; plus the 503 deps-null window.
- Green under the **manual** gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dep, no schema change. `tests/test_adapter_seam.py` stays green (no core edit). **Run the gate yourself — pre-commit test enforcement is intentionally off (see gotcha).**

### References
- [Source: epics.md:381-401] — Story 2.3 user story + all 3 BDD ACs verbatim (`/state` from cache, snake_case + `generated_at`, no git on poll; Origin 403/allow same as `/mcp`; read-only, no mutating route).
- [Source: epics.md:327-329] — Epic 2 intent: derive-on-read, "a poll never shells out to git", forward-only order (projection → cache/refresher → endpoint → UI).
- [Source: epics.md:404-474] — 2.4a/b/c own the UI — the scope fence for what 2.3 must NOT build.
- [Source: architecture.md:350-360] — derive-on-read into an in-memory cache; "`/state` is served from this cache — it never shells out to git on a poll"; `generated_at`; stale-on-unavailable kept (interpretation deferred to 2.4c).
- [Source: architecture.md:362-391] — pinned `CacheSnapshot`/`TaskView`/`WorktreeView` shape (the `/state` JSON contract; `dataclasses.asdict` yields it).
- [Source: architecture.md:408-416] — no auth; bind 127.0.0.1; Origin middleware over `/mcp` AND all dashboard routes; absent Origin allowed.
- [Source: architecture.md:775-777, 793, 811-812] — API boundary: `/mcp` + dashboard routes `/`, `/state`; the read path `browser → GET /state → dashboard/routes → reads cache → JSON`.
- [Source: architecture.md:826] — `dashboard/static/` (assets shipped in the package) — for 2.4a+, not 2.3.
- [Source: src/dev_helper_mcp/server_factory.py:188-220] — `create_app`, `_DepsHolder`, the lifespan, `Mount("/", app=mcp_app)`, Origin middleware wiring (the route list 2.3 edits).
- [Source: src/dev_helper_mcp/middleware.py:35-49] — `OriginValidationMiddleware` (parent-outermost; already guards `/state`).
- [Source: src/dev_helper_mcp/config.py:9-28] — `APP_NAME`, `MCP_PATH="/mcp"`, `ALLOWED_ORIGIN_HOSTS` (the Origin allowlist hosts).
- [Source: tests/test_server_factory.py, tests/test_middleware_origin.py] — existing mount/no-307 + Origin matrix tests to keep green / extend with `/state`.
- [Source: tests/test_adapter_seam.py] — `SEAM_MODULES` = `core/`/`git/`/`store`/`projection`/`cache`; `dashboard/` is NOT scanned (adapter, allowed SDK).
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — "`Mount('/', app=mcp_app)` shadows future routes … Epic 2's dashboard (`/state` + board at `/`) must revisit it … Deferred to Epic 2" (Task 4's mandate); the no-`-z` and `Mount` notes.
- [Source: 2-1-derive-on-read-projection-pure-task-grouped.md] — `CacheSnapshot` shape + `dataclasses.asdict()` is the snake_case `/state` payload.
- [Source: 2-2-in-memory-cache-and-background-refresher.md] — `Cache.current` (by-ref, GIL-atomic), warm-start, `ToolDeps.cache`, post-mutation refresh (so `/state` reflects mutations without 2.3 refreshing); `repo_unavailable:`/`orphan_link:` warnings carried in the snapshot (interpreted by 2.4c).
- [Source: project-context.md] — SDK seam, snake_case/derive-on-read contract, Origin/127.0.0.1 security, in-process ASGI testing rules (lifespan-context, `http://127.0.0.1:<port>`, no `pytest-asyncio`), git-safety-in-tests, "this file wins over architecture pseudo-code", the enforced quality gate.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Opus 4.8, 1M context) — via the BMad dev-story workflow.

### Debug Log References

- Full manual gate: `uv run ruff check .` (All checks passed) + `uv run ruff format --check .` (44 files formatted) + `uv run pytest -m "not slow"` → **209 passed, 5 deselected** in ~114s.
- Targeted: `tests/test_dashboard_state.py` (5) + `tests/test_middleware_origin.py` (9 incl. new `/state` rows) + `tests/test_server_factory.py` (regression, no-307) + `tests/test_adapter_seam.py` — all green.

### Completion Notes List

- **All 3 ACs satisfied.** `/state` serves `dataclasses.asdict(Cache.current)` as snake_case JSON (AC1), guarded by the existing outermost Origin middleware with a new `/state` matrix row (AC2), and is read-only — non-GET is rejected without mutation and the route table exposes no mutating dashboard route (AC3). Decision A 503 deps-null window covered.
- **Route-shadowing fix (Task 4) landed via route ordering.** `state_route(holder)` is listed **before** the catch-all `Mount("/", app=mcp_app)` in `create_app`, sharing the **same** `holder` instance as the tool closures. `/mcp` still resolves through the Mount with no 307 (existing `test_server_factory.py` stays green). A load-bearing comment referencing deferred-work.md warns against "tidying" the order.
- **⚠️ Discovery — non-GET yields 404, not the guidance's "automatic 405".** With `methods=["GET"]`, Starlette 1.3.1 returns only a *partial* match for POST/PUT/DELETE on `/state`; the catch-all `Mount("/")` behind it returns a *full* match and short-circuits, so a non-GET falls through to the MCP app → **404**. The "automatic 405" in Task 3/Task 6 guidance rested on a routing assumption the implementation disproved (the same `Mount("/")` shadowing Task 4 fixes for GET only). I honored the explicit "methods=['GET'] only, do not add any other method" instruction rather than hand-rolling a 405, and the **binding AC3 criterion** ("no mutating route or action") is fully met — a non-GET to `/state` mutates nothing (proven by the route-table assertion + the 404/405 rejection test). This is a "reality differs from pseudo-code" case per project-context.md.
- **No git on the poll path (Invariant 4) is asserted**, not just claimed: a spy on `GitRunner.run_git` records zero calls during the bare `GET /state`, and the test also drives a `create_task` (against `tmp_git_repo` only) to prove `/state` reflects the live, post-mutation cache.
- **Scope held exactly.** No HTML/CSS/JS, no `/` index, no change to `cache.py`/`projection.py` (verified absent from the diff), no new middleware, no new dependency, no schema/migration. `dataclasses.asdict` at the route boundary is the whole transform.
- **Circular-import avoidance:** `dashboard/routes.py` imports `_DepsHolder` only under `TYPE_CHECKING` (server_factory imports the routes module at runtime).

### File List

- **NEW** `src/dev_helper_mcp/dashboard/__init__.py` — dashboard adapter package marker.
- **NEW** `src/dev_helper_mcp/dashboard/routes.py` — `state_route(holder)` factory + the read-only `/state` GET handler (`dataclasses.asdict(cache.current)`; 503 deps-null guard).
- **NEW** `tests/test_dashboard_state.py` — AC1 (shape/by-ref/no-git/reflects-mutation), AC3 (non-GET rejected, route-table read-only), Decision A 503.
- **UPDATE** `src/dev_helper_mcp/server_factory.py` — import `state_route`; add it to `routes=[…]` before the catch-all `Mount("/")` with a load-bearing comment.
- **UPDATE** `tests/test_middleware_origin.py` — add `/state` to the Origin matrix `ROUTES` (AC2).

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-25 | Story 2.3 drafted (ready-for-dev): read-only `/state` GET endpoint in a new `dashboard/` adapter package, serving `dataclasses.asdict(cache.current)` (snake_case, by-ref, no git on poll); route ordered before the catch-all `Mount("/")` to fix the deferred route-shadowing; Origin middleware covers it for free; read-only (405 on non-GET). Hard prerequisite: Stories 2.1 + 2.2 implemented first. Decision A (deps-null window → **503**) operator-confirmed. Note: pre-commit `pytest` enforcement was intentionally removed — the gate is a manual command; run it yourself. |
| 2026-06-25 | Story 2.3 implemented (status → review). New `dashboard/{__init__,routes}.py` + `tests/test_dashboard_state.py`; `server_factory.create_app` route list reordered (`state_route(holder)` before `Mount("/")`); `tests/test_middleware_origin.py` extended with a `/state` row. **Implementation finding:** non-GET `/state` returns 404 (not the guidance's 405) because the catch-all `Mount("/")` full-matches non-GET verbs and short-circuits before the GET-only route's partial 405 — AC3's binding "no mutating route or action" criterion is still fully satisfied (proven by the route-table assertion). Full manual gate green: ruff check + format clean, 209 passed / 5 slow deselected. `projection.py`/`cache.py` unchanged; adapter seam green; no new dependency. |

## Review Findings (Code Review 2026-06-26)

_3 review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). All 3 ACs + Decision A confirmed SATISFIED; scope fences and SDK seam honored. 2 patch, 1 defer, 9 dismissed as noise._

- [x] [Review][Patch] `state_route` docstring claims non-GET yields "Starlette's automatic 405" — reality is **404** via the catch-all `Mount("/")` fallthrough (the story's own Dev Record + tests already document 404; this lone comment is stale and contradicts them) [src/dev_helper_mcp/dashboard/routes.py:36-37] — FIXED: docstring rewritten to describe the PARTIAL-match → Mount-fallthrough → 404 reality.
- [x] [Review][Patch] `test_non_get_methods_are_rejected_without_mutation`: the `assert resp.status_code >= 400` line is tautological after `in (404, 405)`, and accepting `(404, 405)` masks that the outcome is *always* 404 (Mount fallthrough). Tighten to `== 404` to pin the real contract; the verb test never asserts the store/cache is actually unchanged (read-only is structurally proven only by `test_route_table_is_read_only`) [tests/test_dashboard_state.py:137-138] — FIXED: tightened to `assert resp.status_code == 404` with a corrected comment; dead `>= 400` line removed.
- [x] [Review][Defer] `GET /state/` (trailing slash) returns `Match.NONE`, falls through to the MCP `Mount("/")` → opaque 404, no 307 redirect to `/state`; untested [src/dev_helper_mcp/dashboard/routes.py:51] — deferred, out of this story's exact `/state` contract; flag for the Story 2.4b poller
