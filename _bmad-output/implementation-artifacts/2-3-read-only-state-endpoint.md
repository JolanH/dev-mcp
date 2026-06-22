# Story 2.3: Read-only `/state` endpoint

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard client,
I want a `/state` JSON endpoint served from the in-memory cache,
so that I can poll current state cheaply and safely without triggering any git work or mutation.

## Acceptance Criteria

1. **`/state` reads the cache only — no git on the poll path (FR-9, Invariant 5).**
   **Given** the in-memory cache,
   **When** `GET /state` is called,
   **Then** it returns the current `CacheSnapshot` as **snake_case JSON** including `generated_at`, read from the cache **by reference only** — no git shell-out on the poll path.

2. **Origin validation on `/state` (NFR-Security, AR-4).**
   **Given** any dashboard route including `/state`,
   **When** a request carries a non-allowlisted `Origin`,
   **Then** it is rejected `403` by the same outermost middleware as `/mcp`;
   **And** an absent `Origin` is allowed.

3. **Read-only guarantee asserted (FR-10).**
   **Given** the dashboard's served interface,
   **When** it is inspected,
   **Then** it exposes no mutating route or action (no create/modify/remove of worktrees or tasks, no agent launch) — the read-only guarantee is asserted by test.

## Tasks / Subtasks

- [ ] **Task 1 — `dashboard/routes.py`: `GET /state` (AC: 1)**
  - [ ] Read `cache.get().current` by reference; serialize the `CacheSnapshot` to **snake_case JSON** (incl. `generated_at`) — the dashboard JS reads snake_case keys, no translation layer
  - [ ] **No git shell-out, no DB read, no mutation** on this path — cache ref read only; a `GET` only
  - [ ] Serialize the frozen dataclasses (e.g. `dataclasses.asdict` / a small to-dict) preserving the exact 2.1 field names; booleans as JSON `true`/`false`
- [ ] **Task 2 — register routes on the app-owned Starlette app (AC: 1, 2)**
  - [ ] Add the `/state` route (and a placeholder `GET /` if not already present — the real board HTML is 2.4a) to the Starlette routes in `server_factory.py`, **under the same outermost Origin middleware** that guards `/mcp`
  - [ ] Confirm the Origin allowlist (port-aware, from 1.1) covers dashboard routes — it already sits above both `/mcp` and dashboard routes by design
- [ ] **Task 3 — tests (under AR-12 gate; in-process `httpx.ASGITransport`)**
  - [ ] `GET /state` returns the current snapshot as snake_case JSON with `generated_at`; assert no git is invoked on the poll path (e.g. spy on `run_git` → not called)
  - [ ] Origin matrix on `/state`: non-allowlisted → 403; absent → allowed; allowlisted → allowed
  - [ ] Read-only guarantee: enumerate the served routes/methods → no mutating route/method exists (only `GET` on `/` and `/state`); the interface offers no create/modify/remove/launch

## Dev Notes

### Scope boundaries — read first
Exposes the cache (2.2) over HTTP as a read-only JSON endpoint. **OUT of scope:** the HTML page, CSS, and JS poller (Stories 2.4a–c — `GET /` may return a minimal placeholder here; the real board markup is 2.4a). No new state, no git, no mutation.

### Binding invariants this story enforces
- **Invariant 5 — `/state` reads the in-memory cache only; never shells out to git on a poll.** This is the load-bearing performance guarantee — assert it by test (spy that `run_git` is not called on the `GET /state` path). [Source: architecture.md#Invariants; #Derived State & Refresh Model]
- **Invariant 9 — Origin middleware is outermost over `/mcp` AND dashboard routes.** `/state` is a dashboard route; it inherits the same chokepoint. Do not add a second/duplicate middleware. [Source: architecture.md#Invariants; #Authentication & Security]
- **Invariant 3 — snake_case JSON everywhere** (tool I/O AND `/state`); no case-translation layer. [Source: architecture.md#Invariants; #Data & Format Patterns]
- **FR-10 read-only:** the dashboard renders state, never mutates — no mutating route/action. [Source: epics.md#FR-10; EXPERIENCE.md#Foundation]

### Builds on Stories 1.1 + 2.2 (previous-story intelligence)
- From **1.1**: the app-owned Starlette app, the outermost port-aware Origin middleware, and the `Mount("/mcp")` topology already exist. Add `/state` to the existing route list — do not create a second app or a second middleware.
- From **2.2**: read the cache via its `get()` accessor (by reference, no lock). The cache is already populated by the background tick + mutation refreshes; `/state` is a pure consumer.
- `dashboard/routes.py` is **adapter/presentation layer** — it may import `starlette` (it serves HTTP) but contains **no git/DB logic** (data boundary). Keep `test_adapter_seam.py` green for the *core* modules (routes is adapter, so it's allowed `starlette`).

### Data flow (architecture.md § Integration Points)
`browser → GET /state → dashboard/routes → reads cache (in-memory) → JSON {…, generated_at}.` The poll path never touches git. [Source: architecture.md#Integration Points & Data Flow]

### Source tree components to touch
`dashboard/routes.py` (new — `GET /state`, placeholder `GET /`), register routes in `server_factory.py`; `test_middleware_origin.py` (extend to cover `/state`) + a read-only/`/state` test (e.g. in a `test_dashboard.py` or extend `test_server_factory.py`). [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping]

### Project Structure Notes
- `dashboard/` is the presentation layer; `routes.py` serves read-only routes. Static assets (`index.html`, `app.js`, `style.css`) come in 2.4a–c. [Source: architecture.md#Complete Project Directory Structure]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 2.3: Read-only `/state` endpoint] — acceptance criteria
- [Source: epics.md#FR-9] auto-refresh; [Source: epics.md#FR-10] read-only guarantee
- [Source: architecture.md#Invariants] — invariants 3, 5, 9
- [Source: architecture.md#Integration Points & Data Flow] — dashboard poll path
- [Source: EXPERIENCE.md#Foundation] — read-only, no external assets, localhost

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
