---
baseline_commit: d799578ff010566deb51500606b76d988c390b9d
---
# Story 2.4b: Live poller with diff-and-patch stable render

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want the open board to track `/state` live without flicker or losing my place,
so that I can leave the tab open and trust it to silently stay correct.

## Acceptance Criteria

1. **Given** the page open,
   **When** an agent updates a task's status,
   **Then** the page reflects it within ≤ 3s (≤ 15 repos) with no manual reload (vanilla-JS poll of `/state` ~1–2s).

2. **Given** the poller's `diff(prev, next)` function keyed by `task_id` with a per-task content hash,
   **When** two identical `/state` snapshots are diffed,
   **Then** it returns an empty patch set (`diff(x, x) === []`) — asserted by a `node --test` unit test — **and** a `MutationObserver` over the board container records **zero** mutations across the identical poll.

3. **Given** a task whose status changes between polls,
   **When** the patch applies,
   **Then** the existing DOM node is **reparented** to the new column (not destroyed/recreated), only changed fields are patched, and the open/closed state of the Done/orphan disclosures and any scroll position are preserved. **(UX-DR5)**

4. **Given** the served UI,
   **When** the user interacts with it,
   **Then** it offers no control to create/modify/remove worktrees or tasks or to launch agents (read-only). **(FR-10)**

## ⛔ HARD PREREQUISITE — read before anything else

**Story 2.4b cannot be implemented until Stories 2.1, 2.2, 2.3, and 2.4a are implemented.** It adds the live poll loop + diff-and-patch over the static board 2.4a renders, polling the `/state` endpoint 2.3 serves.

- 2.4a (`ready-for-dev`) ships `dashboard/render.py` (`render_board(snapshot)->str`), `dashboard/tokens.py`, the `/` board route, and the **markup contract**: each card is `<div class="card {status}" data-status="{status}" data-task-id="{id}">` with a per-card glyph, `.t` title row, optional `.badge`, and `.wt` worktree lines; three columns `.col-run/.col-blk/.col-rev`; a `.fold.done` `<details>`; a `.fresh[data-generated-at]` stamp.
- 2.3 (`ready-for-dev`) serves `GET /state` → `dataclasses.asdict(CacheSnapshot)` (snake_case).
- If `dashboard/render.py` / the `/` route do not exist, implement 2.1 → 2.2 → 2.3 → 2.4a first, then return here.
- **2.4b OWNS the client-side JS** (the poller + `diff` + `patch`); it does **not** re-render the whole board and does **not** change the server-side markup contract (2.4a owns that).

## Tasks / Subtasks

- [x] **Task 1 — Add poll tunables to `config.py` and surface them to the page** (AC: 1)
  - [x] `DASHBOARD_POLL_INTERVAL_MS: int = 1500` (~1–2s poll of `/state`; distinct from 2.2's *background* `CACHE_REFRESH_INTERVAL` server tick — this is the **browser** poll). Comment the distinction. (Decision A, operator-confirmed: 1500ms.)
  - [x] The render (`render_board`, 2.4a) must inject this value where the JS can read it — a `data-poll-interval` attribute on a root element, or a small inline `<script>window.__POLL_MS__=…</script>` emitted by the server (still self-contained — UX-DR10). 2.4b extends `render_board` minimally to embed it + to **inline the poller JS** (Task 3).
- [x] **Task 2 — Author the poller JS as a real source file** (AC: 1, 2, 3, 4) — *the heart of the story*
  - [x] `src/dev_helper_mcp/dashboard/static/poller.js` (NEW). Plain ES, no framework, no external import (UX-DR10). It is a **source file** (not inline-authored) so `node --test` can import and unit-test `diff()` (AR-12: "a `node --test` unit test for the poller `diff()` — the one small JS test added to the gate"). It is **inlined into the page** at render time (Task 3) so the served page has no external `<script src>` (UX-DR10).
  - [x] **`diff(prev, next)` — pure, exported, the unit-tested core (AC2):**
    - Both args are `/state` snapshots (`{generated_at, tasks:[…], warnings:[…]}`). Key tasks by `task_id`.
    - For each `task_id`: in `next` not `prev` → `{op:"add", task_id, task}`; in `prev` not `next` → `{op:"remove", task_id}`; in both → compute a **content hash** over the fields that affect rendering (`status`, `description`, the reason badge, and the worktree list `[(repo_path, branch, path, orphaned)…]`, plus the warning-derived per-repo state) — **equal hash → emit nothing**; changed → `{op:"update", task_id, task, changed:{status?, fields?}}`; a `status` change is flagged so `patch` reparents (Task 4).
    - **`diff(x, x)` MUST return `[]`** (deep value-equality via the content hash; order-independent). This is the headline AC2 assertion.
    - **Purity:** `diff` touches no DOM, no clock, no globals — given the two snapshots it returns a patch array, deterministically. (Mirror 2.1's projection purity — it is what makes the `node --test` trivial and total.) Export it for node: `if (typeof module !== "undefined" && module.exports) module.exports = { diff, contentHash };` — harmless when inlined in the browser (where `module` is undefined).
  - [x] **`patch(boardEl, patches, next)` — applies the diff to the live DOM (AC3):** for `add` → build a card node (matching 2.4a's markup contract) and `insertBefore` at the **sorted position** in the target column (tasks sorted by `task_id`); `remove` → `el.remove()`; `update` with a **status change** → move the **existing** node to the new column via `insertBefore` (**reparent, never destroy-and-recreate** — UX-DR5), then patch changed fields; `update` without status change → patch only the changed text/attrs/`.wt` lines on the existing node. Update the column header counts + summary pills from `next`. **Never `innerHTML = …` wholesale.**
  - [x] **`renderCard(task)` helper** — builds a card DOM node identical in structure to 2.4a's server markup (same classes, `data-status`, `data-task-id`, glyph, badge, `.wt` lines). This is the one **markup duplication** with the Python renderer (Decision B) — keep the structure pinned to 2.4a; a shared content-shape comment in both files flags the contract.
  - [x] **Disclosure + scroll preservation (AC3):** because `patch` mutates only changed nodes (never the `<details>` elements or the board wholesale), the Done/orphan `<details>` `open` state and the column scroll positions survive a poll automatically. **Do not** rebuild or toggle the disclosures during a patch. (Belt-and-suspenders: if a patch must touch a disclosure's count, update only its `<summary>` text, never its `open` attr.)
- [x] **Task 3 — Poll loop + inline the JS into the page** (AC: 1)
  - [x] In `poller.js`: `async function poll(){ const next = await fetch("/state").then(r=>r.json()); const patches = diff(window.__prev__, next); if (patches.length) patch(board, patches, next); window.__prev__ = next; }` then schedule the next poll with **`setTimeout(poll, POLL_MS)`** (re-arm AFTER each completes — no overlapping in-flight polls). **Do NOT use `requestAnimationFrame` or a style-mutating timer** (UX-DR4 forbids `requestAnimationFrame`/timer-driven *style* mutation; `setTimeout` for *fetching* is fine — it changes data, not animates). Seed `window.__prev__` from the server-rendered snapshot (embed the initial `/state` JSON in the page, or do one immediate `poll()` that diffs against a parse of the current DOM — embedding the initial JSON is simpler; emit it in a `<script type="application/json" id="initial-state">`).
  - [x] **Inline mechanism (UX-DR10):** extend `render_board` (2.4a) to read `poller.js` from disk (package-relative, e.g. `importlib.resources`) and embed its contents inside an inline `<script>…</script>` — so the served HTML has **no external `src`** yet the file stays unit-testable by node. (Read once at import/first-render; it is a packaged static asset under `dashboard/static/`, architecture.md:826.)
  - [x] **No motion on status swap:** a status change appears as a state swap (reparent + attr change), never a transition/animation (UX-DR4; EXPERIENCE.md:76).
- [x] **Task 4 — Reparent-not-recreate + field patching (AC3)**
  - [x] On a status change, locate the existing node by `[data-task-id="…"]`, update its `data-status` + `class` (`card run|blk|rev` + glyph + badge), and `insertBefore` it into the new column at the sorted slot. **The same DOM node object** moves columns — assert in tests that node identity is preserved (e.g. tag it and confirm the tag survives the reparent). This preserves focus/scroll and is the UX-DR5 "reparent (not recreate)" predicate.
  - [x] Field-only updates (description, badge text, a worktree line going `orphaned`/`unavailable`) patch the specific child node's text/attr — not the card.
- [x] **Task 5 — `node --test` for `diff()` as part of the manual gate (AR-12)** (AC: 2)
  - [x] `tests/js/diff.test.mjs` (or `dashboard/static/poller.test.mjs`) — Node's built-in test runner (`node --test`, available: node v20.19.4). Import `diff`/`contentHash` from `poller.js`. **Assertions:** `diff(x, x)` deep-equals `[]` (identical snapshots → empty patch); an add/remove/status-change/field-change each yields exactly the expected patch op; the content hash is order-independent for worktrees (already sorted, but assert stability). **No new npm dep, no `package.json` required** for pure `diff` tests — `node --test` + `node:assert` + `node:test` are built in. (Decision C: dependency-free; the 0-mutations check is a dep-free spy — no jsdom, see Task 6.)
  - [x] **The `node --test` runs as part of the MANUAL gate command — do NOT wire it into `.githooks/pre-commit`.** Pre-commit enforcement of the test suite was **intentionally removed** (operator decision 2026-06-25); the hook runs only `ruff`. The v1 quality gate is a manual command. Document and run: `node --test tests/js/` alongside `uv run pytest -m "not slow"`. Do not add a pytest or node step to the hook. (Decision C: `node --test` is the AR-12 gate test, run manually.)
- [x] **Task 6 — Zero-DOM-write test on identical poll (AC2, UX-DR5) — dep-free spy (Decision D)**
  - [x] The predicate: across an **identical** consecutive `/state`, **zero** DOM mutations occur. **Realize it dependency-free (no `jsdom`):** in `node --test`, spy on `patch`/`applyPatch` (or structure the poll so `patch` is only called when `diff` is non-empty) and assert that an identical-snapshot poll **never invokes `patch`** — empty diff ⇒ no DOM write ⇒ 0 mutations by construction.
  - [x] The **`diff(x,x)===[]` `node --test` is the primary, dependency-free machine check**; the never-call-`patch` spy is the corroborating UX-DR5 assertion. (A literal `jsdom`+`MutationObserver` test is intentionally NOT added — Decision D.)
- [x] **Task 7 — Live-update + read-only integration tests (AC1, AC3, AC4)**
  - [x] **AC1 live update (in-process ASGI, Python):** open the page implicitly by reading `/state` over the in-process client; `create_task`/`update_task(status=…)` against a `tmp_git_repo`; poll `/state` again and assert the new/changed task appears in the payload within the budget (the *client* ≤3s is bounded by the poll interval + 2.2's ≤3s fan-out; here assert the payload reflects the mutation after the 2.2 post-mutation refresh). The DOM-patch behavior itself is covered by the `node --test` diff/spy tests (browser-free). (Wrap in `async with app.router.lifespan_context(app):`; `http://127.0.0.1:<port>`.)
  - [x] **AC4 read-only (FR-10):** static lint over the rendered page + `poller.js`: the poller only **`fetch("/state")` with GET** (no POST/PUT/DELETE/PATCH `fetch`, no form submit, no mutating MCP call); the page has no create/edit/remove/launch control. Extend `tests/test_dashboard_static_lint.py` (2.4a) to grep `poller.js` for any non-GET fetch / mutation verb → absent.
  - [x] **Static-lint over the inlined JS (UX-DR4/10):** the rendered page (now with inlined poller) still has no `requestAnimationFrame`, no `transition`/`animation`/`@keyframes`, no external `src`/`http(s)://`. Re-run 2.4a's static-lint test against the JS-bearing page.
- [x] **Task 8 — Gate green + seam confirmation** (AC: all)
  - [x] `dashboard/static/poller.js` is a static asset (not Python; not in `SEAM_MODULES`); `render.py` gains only the inline-embed + poll-interval injection (still pure, no `mcp`/`starlette`); core unchanged → `tests/test_adapter_seam.py` green.
  - [x] Full gate (manual): `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` **AND** `node --test tests/js/`. New tooling dep: **node** for the gate command (Decision C; already on the machine). **Decision D: MutationObserver via a dep-free spy** (no `jsdom`). `config.py` gains `DASHBOARD_POLL_INTERVAL_MS`. No schema change, no git command, **no `.githooks/pre-commit` edit** (enforcement intentionally off). ⚠️ Run the gate yourself.

### Review Findings

_Adversarial code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor), 2026-06-26. Baseline `d799578`. 1 decision-needed, 5 patch, 1 deferred, ~10 dismissed as noise/by-design. Triage notes verified against `render.py`/`projection.py`/the v1 tool surface._

- [x] [Review][Patch] DOM patch-layer has no test coverage — `patch`/`reparent`/`insertSorted`/`updateCounts` are exercised by no test, and `patch_spy.test.mjs` re-implements the poll guard (`if (patches.length) patch(...)`) rather than calling the real `patch`, so it proves nothing beyond the existing `diff(x,x)===[]` test. This is the gap that let the two robustness patches below slip the gate. **Decision (resolved 2026-06-26):** add a **dependency-free DOM-stub** `node --test` that exercises `patch`/`reparent` (asserts node identity preserved across a status change, correct sorted slot, count sync) — keeps Decision D's no-`jsdom`/minimal-deps posture intact (jsdom was explicitly declined). [blind] [tests/js/]
- [x] [Review][Patch] Poll loop missing `r.ok`/shape guard — a non-snapshot `/state` body (e.g. the route's `{"detail":"server not ready"}` 503 in the lifespan-teardown window) parses fine, `diff` reads `next.tasks` as `[]`, and the board is wiped (every card removed) then `prev` is poisoned → next good poll re-adds everything (flicker). `.catch` only covers network/parse errors. Fix: `if (!r.ok) throw` and/or skip the diff + do not advance `prev` unless `Array.isArray(next.tasks)`. [blind+edge] [src/dev_helper_mcp/dashboard/static/poller.js: poll()]
- [x] [Review][Patch] Reparent/add to an unknown or `null` status leaves a ghost node + count drift — `containerForStatus` returns null for an out-of-set/`null` status; `reparent` re-shapes the node in place (empty class/glyph) but never moves or removes it, and `prev=next` then cements the desync (a reload would skip it). Defensive only: NOT currently reachable via the v1 tool surface (no delete-task tool; `update_task` rejects out-of-set status; `status=None` only for orphans, which aren't rendered as active cards). Fix: when the destination container is null, `el.remove()` so the patched DOM matches the server's skip. [blind+edge] [src/dev_helper_mcp/dashboard/static/poller.js: reparent/containerForStatus/addCard]
- [x] [Review][Patch] `data-poll-interval` not clamped for negative/tiny values — `parseInt(...) || 1500` catches `0`/`NaN`/empty but a negative (`"-5"`) is truthy → `setTimeout` clamps to ~0 → tight poll loop, defeating the no-overlap/≤3s intent. Requires operator misconfig of the trusted `DASHBOARD_POLL_INTERVAL_MS`. Fix: `Math.max(250, parseInt(...) || 1500)` and/or validate `> 0` in `config.py`. [edge] [src/dev_helper_mcp/dashboard/static/poller.js: startPoller]
- [x] [Review][Patch] GET-only verb lint is case/quote-sensitive — `for verb in ('"POST"', …)` misses lowercase `'post'`, single quotes, template literals, `method:"POST"` without a space; weak proof for the FR-10 GET-only guarantee. Fix: case-insensitive regex `method\s*:\s*['"\x60](post|put|delete|patch)`. [blind] [tests/test_dashboard_static_lint.py: test_poller_fetches_only_get_state]
- [x] [Review][Patch] Static-lint substring greps are overbroad — `"src=" not in LOW` and the `<form`/`<button`/… control greps run over the whole page (incl. inlined JS + embedded JSON), so a data value/JS token containing `src=` or `<input` would false-fail, while `<script src` isn't specifically targeted. Fix: anchor to `"<script src"` and strip `<script>`/JSON blocks before grepping interactive-control tokens. [blind] [tests/test_dashboard_static_lint.py]
- [x] [Review][Defer] `rebuildWorktrees` appends `.wt` lines at the node end — correct for 2.4a markup (`.wt` is the trailing child), but will misorder if 2.4c appends a freshness/footer element after the worktree lines; insert at the recorded position then. Deferred — pre-emptive note for Story 2.4c. [blind] [src/dev_helper_mcp/dashboard/static/poller.js: rebuildWorktrees]

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
2.4b is the **second UI story**: the live `/state` poll loop + the diff-and-patch stable render over 2.4a's static board. It makes the open tab silently track state without flicker or losing the operator's place.

- **BUILD:** `dashboard/static/poller.js` (`diff`, `contentHash`, `patch`, `renderCard`, the poll loop), the inline-embed + poll-interval injection in `render.py`, `DASHBOARD_POLL_INTERVAL_MS` in `config.py`, the `node --test` for `diff()` + its gate wiring, the MutationObserver/spy test, and live-update + read-only tests.
- **DO NOT BUILD (later stories — hard fence):**
  - **No freshness/relative-age display, no stale (grey→amber) treatment, no "git unavailable" banner, no per-repo "unavailable" line rendering, no orphan-disclosure population, no empty-state copy, no zero-done-omit** → **Story 2.4c**. 2.4b keeps the board *current*; interpreting freshness/degrade/empty is 2.4c. (2.4b DOES poll the `generated_at` and pass it through, but does not yet style staleness.)
  - **No change to the server markup contract** (2.4a owns the card/column/disclosure shape) — `renderCard` must MATCH it, not redefine it.
  - **No change to `/state` (2.3), `cache.py`/`projection.py` (2.1/2.2)** — 2.4b is client-side + a thin render embed.
  - **No SPA, no build step, no bundler, no npm runtime dep** — vanilla ES, inlined; `node --test` is test-only.
  - **No mutating control** (FR-10) — the poller is GET-only.
- [Source: epics.md:428-451 (this story); epics.md:404-427 (2.4a markup); epics.md:452-474 (2.4c edge states); EXPERIENCE.md:68-77 (interaction primitives, diff-and-patch contract).]

### ✅ Decision A — poll interval (OPERATOR-CONFIRMED 2026-06-25: 1500ms)
EXPERIENCE.md:70 says "fetch `/state` every ~1–2s. Target freshness ≤3s". `DASHBOARD_POLL_INTERVAL_MS=1500` sits mid-range and keeps end-to-end staleness (server tick 2.0s + poll 1.5s, overlapping) within the ≤3s SLO for ≤15 repos. It is the **browser poll**, distinct from 2.2's `CACHE_REFRESH_INTERVAL=2.0` **server tick**. **DECIDED: 1500ms.**

### Decision B — JS `renderCard` duplicates 2.4a's markup (accepted, pinned)
Diff-and-patch needs the client to **build** card nodes for tasks that appear mid-session, so `poller.js` has a `renderCard(task)` that mirrors the Python `render_board` card markup. This duplication is inherent to "server renders initial + client patches live" (2.4a Decision A). Mitigation: the card structure is small and **pinned by 2.4a's contract** (classes, `data-status`, `data-task-id`, glyph, `.badge`, `.wt`); add a comment in BOTH `render.py` and `poller.js` naming the other as the contract sibling. A render-parity test (optional) can assert a server-rendered card and a `renderCard` card have the same tag/class/attr skeleton.

### ✅ Decision C — `node --test` as the manual-gate JS test (OPERATOR-CONFIRMED 2026-06-25)
AR-12 mandates "a `node --test` unit test for the poller `diff()` … the one small JS test added to the gate." node v20.19.4 is present; `node --test` + `node:test`/`node:assert` are **built in** (no npm install, no `package.json` needed for the pure `diff` test). **DECIDED: `node --test tests/js/` is part of the v1 MANUAL gate command** (alongside `uv run pytest`). It is **not** wired into `.githooks/pre-commit` — pre-commit test enforcement was intentionally removed; the gate is run manually. node is the only added gate tool (already on the machine).

### ✅ Decision D — MutationObserver test realization (OPERATOR-CONFIRMED 2026-06-25: dep-free spy)
The UX-DR5 "MutationObserver records 0 mutations on identical poll" predicate needs a DOM. **DECIDED (dep-free): a spy proving `patch` is never invoked when `diff` is empty** (empty diff ⇒ zero DOM writes by construction) — **no `jsdom` dependency** (keeps the minimal-deps posture). The dependency-free `diff(x,x)===[]` `node --test` is the primary, machine-checkable assertion; the spy corroborates UX-DR5. (A literal `jsdom`+`MutationObserver` test is *not* added.)

### The diff-and-patch contract (the mechanical heart — EXPERIENCE.md:71-75)
The poller does **not** replace `innerHTML`. It holds the last snapshot and computes a per-`task_id` delta:
- key absent in new → remove that node; key new → create + insert in **sorted** position;
- key in both → compare a per-task **content hash** (status, slug/description, repos, reason, freshness-relevant fields); **equal → no DOM write at all**; changed → patch only the changed fields on the existing node;
- a **status change reparents** the existing node to the new column (`insertBefore`), never destroy-and-recreate.
This preserves the Done/orphan `<details>` open-state and scroll across polls. **Testable:** `diff(x,x) === []` (`node --test`) + a `MutationObserver` over the board records 0 mutations on an identical poll. (EXPERIENCE.md:71-75, UX-DR5.)

### What the substrate already gives you (consumed contracts)
- **2.4a markup contract** — card `<div class="card {status}" data-status data-task-id>`, `.t/.g/.badge/.wt`, columns `.col-run/.col-blk/.col-rev`, `.fold.done` `<details>`, `.fresh[data-generated-at]`. `patch`/`renderCard` target exactly these. [2.4a Task 2/Dev Notes#render contract]
- **`/state`** (2.3) — `GET` → `asdict(CacheSnapshot)` snake_case; the poller `fetch`es it. Read-only, no git on the poll (2.3 guarantees the server side; the poller just GETs). [2.3]
- **2.2 post-mutation refresh** — a `create_task`/`update_task`/`remove_worktree` refreshes the cache before returning, so the very next `/state` poll reflects it (AC1). [2.2 AC2]
- **`render_board`** (2.4a) — extend it to (a) inject `DASHBOARD_POLL_INTERVAL_MS`, (b) embed the initial `/state` JSON (`<script type="application/json">`), (c) inline `poller.js`. Keep it pure (read the JS file via `importlib.resources` at render — package data). [2.4a]
- **config.py** pattern — all tunables here; add `DASHBOARD_POLL_INTERVAL_MS`. [config.py:1-3]
- **In-process ASGI + tmp-repo fixtures** (conftest.py) — for the AC1 live-update Python test. node tests stand alone.

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 3 — snake_case** — the JS reads snake_case keys (`task_id`, `repo_path`, `generated_at`) directly; no translation. [architecture.md:67]
- **Invariant 7 — SDK seam** — `poller.js` is a static asset; `render.py` stays pure (no `mcp`/`starlette`); the embed reads a packaged file, not the SDK. [architecture.md:71]
- **No motion (UX-DR4)** — no `requestAnimationFrame`, no CSS transition/animation; a status change is a state swap. `setTimeout` for *polling* (data fetch) is allowed; timer-driven *style* mutation is not. [DESIGN.md:66, 116; EXPERIENCE.md:76]
- **Self-contained (UX-DR10)** — the poller is **inlined** (no external `<script src>`), system fonts, no egress beyond `fetch("/state")` on localhost. [EXPERIENCE.md:19; NFR-Security/Locality]
- **Read-only (FR-10)** — the poller GETs only; no mutating control. [epics.md:449-450]
- **Stable render (UX-DR5)** — diff-and-patch; identical snapshot → 0 DOM writes; status change → reparent; disclosures/scroll preserved. [EXPERIENCE.md:71-75]

### Critical gotchas (carry into implementation)
- **⚠️ The gate is a manual command; pre-commit test enforcement was intentionally removed — do not restore it.** `.githooks/pre-commit` runs only `ruff`. This story's `node --test` and the Python suite are run **manually**: `uv run pytest -m "not slow"` + `node --test tests/js/`. Do NOT add a pytest or node step to the hook (operator decision 2026-06-25). The node test must be real and runnable (`node --test tests/js/`).
- **`diff(x, x)` MUST be `[]`.** This is the load-bearing AC2 assertion and the basis for "0 DOM writes". A content hash that includes a non-deterministic field (e.g. an unsorted worktree list, or `generated_at` itself) would make identical snapshots diff non-empty and cause needless DOM churn. **Exclude `generated_at` from the per-task content hash** (it changes every poll but must NOT trigger a task re-render — freshness is handled separately in 2.4c). Sort worktrees in the hash (2.1 already sorts them).
- **Reparent, don't recreate.** A status change must MOVE the existing node (`insertBefore`), preserving node identity — recreating it would lose focus/scroll and defeat UX-DR5. Test node-identity survival across a reparent.
- **Preserve `<details>` open-state + scroll.** Because patch never rebuilds the disclosures or the board wholesale, these survive for free — do NOT add code that toggles `open` or rewrites the board container. Only update a `<summary>` count text if needed.
- **No overlapping polls.** Re-arm with `setTimeout(poll, MS)` *after* each poll resolves (in a `finally`), not `setInterval` — a slow `/state` must not stack requests.
- **Inline, not external.** `poller.js` is a real file for `node --test`, but the **served page embeds its contents inline** (UX-DR10). Don't add `<script src="/static/poller.js">` — that is an external asset and an extra route, both forbidden by the self-contained rule.
- **node export guard.** `if (typeof module !== "undefined" && module.exports) module.exports = {diff, contentHash}` lets node import it; in the browser `module` is undefined so the line is a harmless no-op. (Or use an `.mjs` with `export` + a `<script type="module">` inline — but inline modules can't be imported by node the same way; the CommonJS guard inlined in a classic `<script>` is simplest.)
- **fetch only GET `/state`.** Any non-GET fetch or mutation verb in `poller.js` fails the read-only lint (AC4). The poller never calls a tool.

### 🛑 Git safety in tests — HARD RULE
The `node --test` (diff + spy) tests spawn **no git** (pure JS over snapshot objects). The one git surface is the AC1 live-update Python test, which MUST `create_task`/`update_task` against a `tmp_git_repo` (never the project repo; autouse `_guard_project_repo_untouched` + `test_git_safety.py` enforce it). (project-context.md#Git safety in tests.)

### Previous-story (2.4a/2.3/2.2/2.1) intelligence that applies directly
- **2.4a chose server-side render** so the board is correct pre-JS; 2.4b layers the poller on top. `renderCard` mirrors 2.4a's markup (Decision B). [2.4a Decision A]
- **`generated_at` changes every poll** but must NOT churn task DOM — exclude it from the task content hash; freshness is a separate, subordinate concern (2.4c). [EXPERIENCE.md:46, 71-75]
- **`done` tasks live in the disclosure, not a column** — a task transitioning to `done` reparents from its column into the done-disclosure body; transitioning out of `done` is impossible (terminal) so that direction won't occur. [2.4a; FR-6 done-terminal]
- **Test style proven 1.1–2.4a:** plain `pytest` + in-process ASGI for the Python AC1 test; the JS tests use node's built-in runner. No `pytest-asyncio`. [project-context.md#Testing rules]
- **"This file wins over architecture pseudo-code."** The diff-and-patch contract is specified in EXPERIENCE.md (the binding UX spec) — follow it over any looser architecture prose. [project-context.md#Usage Guidelines]

### Git / recent-work intelligence
- **Baseline `cc6c8fe` ("1-6 complete").** Epic 2 prior: 2.1/2.2/2.3/2.4a drafted `ready-for-dev`. `dashboard/{tokens,render,routes}.py` exist (2.3/2.4a); 2.4b adds `dashboard/static/poller.js`, the JS test, and small `render.py`/`config.py` edits.
- **Commit cadence:** one commit per story after a green (manual) gate + adversarial review. Files: NEW `dashboard/static/poller.js`, `tests/js/diff.test.mjs` (+ the dep-free spy test); UPDATE `dashboard/render.py` (inline embed + poll-interval inject + initial-state JSON), `config.py` (+`DASHBOARD_POLL_INTERVAL_MS`), `tests/test_dashboard_static_lint.py` (+JS lint). **No `.githooks/pre-commit` edit** (enforcement intentionally off). No core/store/projection/cache change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100. **node v20.19.4** — built-in `node --test`, `node:test`, `node:assert`, ESM/CJS both supported; no `package.json` needed for the dep-free `diff` test.
- **No npm/runtime JS dep, and no `jsdom`.** Vanilla ES, inlined; the 0-mutations check is a dep-free spy (Decision D). `node --test` is built-in.
- **`importlib.resources`** to read `poller.js` as packaged data inside `render_board` (Python 3.14; the file ships under `dashboard/static/`, architecture.md:826). Ensure `uv_build` includes the static asset in the package (it lives under `src/dev_helper_mcp/dashboard/static/`).

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/dashboard/static/poller.js` (the poller; unit-tested by node, inlined at serve). `tests/js/diff.test.mjs` (node --test for `diff`) (+ optional MutationObserver test).
- **UPDATE:** `src/dev_helper_mcp/dashboard/render.py` (inline `poller.js`, inject `DASHBOARD_POLL_INTERVAL_MS`, embed initial `/state` JSON), `src/dev_helper_mcp/config.py` (+`DASHBOARD_POLL_INTERVAL_MS`), `tests/test_dashboard_static_lint.py` (lint the inlined JS for non-GET fetch / motion / external assets). **NOT `.githooks/pre-commit`** — pre-commit test enforcement is intentionally off; the `node --test` runs in the manual gate.
- **UNCHANGED (do not edit):** `dashboard/routes.py` (2.3/2.4a), `dashboard/tokens.py` (2.4a), `cache.py`, `projection.py`, `store.py`, all `core/`, `git/`, `middleware.py`, `errors.py`, `util.py`, `tools/`, `server_factory.py` (the `/` and `/state` routes already exist). **DB schema unchanged.**
- **DEFERRED, do NOT create or pull forward:** freshness/relative-age + stale treatment, "git unavailable" banner, per-repo "unavailable" lines, orphan-disclosure population, empty-state copy, zero-done-omit (all **2.4c**). [epics.md:452-474]
- Test mirrors src: the JS test sits under `tests/js/` (or beside the asset); architecture's planned posture is "a `node --test` unit test for the poller `diff()`" added to the gate. [AR-12; EXPERIENCE.md:102, 108]

### Testing standards
- **`node --test`** (built-in, node v20): `diff(x,x)===[]`, per-op patch shapes, order-independent content hash. Run: `node --test tests/js/`. Dependency-free.
- **0-mutations spy** (Decision D, dep-free): identical poll ⇒ empty diff ⇒ `patch` never called (no jsdom).
- **Python (in-process ASGI):** AC1 live-update (mutation reflected in next `/state` within budget, against `tmp_git_repo`); AC4 read-only (no mutating control / non-GET fetch); static-lint of the JS-bearing page (no motion / no external asset / GET-only fetch). Wrap in lifespan-context; `http://127.0.0.1:<port>`.
- **Coverage to the four ACs:** (1) poll reflects a status change ≤ poll interval + server tick; (2) `diff(x,x)===[]` (node) + MutationObserver/spy 0-writes; (3) reparent preserves node identity + disclosures/scroll, only-changed-fields patched; (4) GET-only, no mutating control.
- Green under the **manual** gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` **and** `node --test tests/js/`. `tests/test_adapter_seam.py` green. **Run the gate yourself — pre-commit test enforcement is intentionally off; do not wire these into the hook.**

### References
- [Source: epics.md:428-451] — Story 2.4b user story + all 4 BDD ACs verbatim (≤3s live poll; `diff(x,x)===[]` via `node --test` + MutationObserver 0 mutations; reparent-not-recreate + disclosure/scroll preserved; read-only). Maps to UX-DR5 + FR-9/FR-10.
- [Source: epics.md:91, 108] — UX-DR5 (stable render via diff-and-patch) ownership = 2.4b; the `diff()`/MutationObserver predicate.
- [Source: ux-designs/.../EXPERIENCE.md:68-77] — Interaction Primitives: polling cadence, the diff-and-patch mechanical contract (key by `task_id`, content-hash, equal→no write, status→reparent), disclosure/scroll preservation, no motion, the testable predicate.
- [Source: ux-designs/.../EXPERIENCE.md:102, 108-110] — browser-free test strategy; UX-DR5 predicate; the `node --test` for `diff()`.
- [Source: ux-designs/.../DESIGN.md:66, 116] — no motion ("no pulses, spinners, or transitions on poll").
- [Source: architecture.md:826] — `dashboard/static/` (shipped assets served by Starlette) — where `poller.js` lives.
- [Source: architecture.md:67, 71] — Invariant 3 (snake_case the JS reads) + Invariant 7 (SDK seam; render stays pure, poller is a static asset).
- [Source: 2-4a-static-board-structure-status-encoding.md] — the markup contract `renderCard` must mirror; `render_board` to extend (inline embed); Decision A (server-side render); the static-lint test to extend.
- [Source: 2-3-read-only-state-endpoint.md] — `/state` GET → `asdict` payload the poller fetches; read-only guarantee.
- [Source: 2-2-in-memory-cache-and-background-refresher.md] — post-mutation refresh (so the next poll reflects a mutation — AC1); `CACHE_REFRESH_INTERVAL` (the server tick, distinct from the browser poll).
- [Source: src/dev_helper_mcp/config.py:1-3, 30-44] — tunables-in-config; the existing pool/tick constants (the browser poll is a new, distinct constant).
- [Source: .githooks/pre-commit] — the hook runs only `ruff`; pre-commit test enforcement was intentionally removed (operator, 2026-06-25). The `node --test` and `pytest` run as a MANUAL gate command, not in the hook.
- [Source: project-context.md] — SDK seam, snake_case, self-contained/no-egress, no-motion, read-only, minimal-deps posture, testing rules, git-safety, the quality gate, "this file wins over architecture pseudo-code".

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- Static-lint friction (resolved): the inlined poller's COMMENTS originally contained
  the literal tokens `requestAnimationFrame`, `setInterval`, `animation`, and `<script
  src>` — each is a forbidden substring in `test_dashboard_static_lint.py` (the lint is
  a pure substring grep over the whole page). Reworded the comments to avoid the literal
  tokens (e.g. "frame-timer redraw callbacks", "repeating-interval timer", "visual motion
  effect", "external script-src attribute") while keeping the meaning. `setTimeout` is now
  expected in the page (the poll re-arm) — the 2.4a `test_no_timer_driven_style_mutation`
  was split into `test_no_frame_timer_or_interval_style_mutation` (forbids raf/setInterval)
  + `test_settimeout_is_present_and_is_the_poll_rearm`.

### Completion Notes List

- **Pure core (`diff`/`contentHash`/`worktreeKey`) is exported via the CommonJS guard**
  so `node --test` imports it; the DOM layer + poll bootstrap are guarded behind
  `typeof window`/`typeof document` so node never executes them. 16 node tests pass.
- **Content-hash field decision (deviation from the story's literal list, documented):**
  the per-task content hash covers exactly the fields 2.4a's card markup renders —
  `status` (column/class/glyph/badge) and the worktree list `(repo_path, branch, path)`,
  sorted for order-independence. It DELIBERATELY EXCLUDES `generated_at` (the headline
  gotcha — it changes every poll and must not churn task DOM) AND `description` /
  `orphaned` / the other volatile worktree booleans: 2.4a does NOT render those, so
  hashing them would emit `update` patches with no DOM effect ("needless DOM churn",
  contradicting "only changed fields are patched"). 2.4c — which renders freshness +
  orphan/unavailable state — will extend the hash to cover the fields it adds. This
  honors the binding "fields that affect rendering" rule over the Dev-Notes example list.
- **Reparent-not-recreate (AC3):** a status change moves the SAME node object via
  `insertBefore` (node identity preserved → focus/scroll survive). Active↔active updates
  re-shape the node in place to `card {cls}`; a transition INTO `done` re-shapes the same
  node to the `donecard done` structure and moves it into the `<details class="fold done">`
  body at the sorted slot — so a reload equals the patched DOM. `done` is terminal, so
  `done → active` never occurs.
- **Disclosure/scroll preserved for free:** `patch` never rebuilds the board container or
  the `<details>` elements; the done-summary count is updated as `<summary>` TEXT only,
  never touching its `open` attribute (UX-DR5).
- **Zero-DOM-write (AC2/UX-DR5, Decision D — dep-free):** the poll guard is
  `if (patches.length) patch(...)`; an identical poll ⇒ empty diff ⇒ `patch` never called
  ⇒ 0 writes by construction. `tests/js/patch_spy.test.mjs` proves the spy is not invoked
  on identical polls (and IS invoked once on a real change). No jsdom.
- **AC1 (Python, in-process ASGI):** `create_task` + `update_task(status='blocked')`
  against a `tmp_git_repo` are reflected in the next `GET /state` (2.2 post-mutation
  refresh); the served board page inlines the poller, embeds the initial `/state` JSON,
  and injects `data-poll-interval`. Git-safety HARD RULE respected (tmp repo only).
- **AC4 read-only (FR-10):** the poller issues exactly one `fetch("/state")` (GET); the
  lint asserts no non-GET verb / `XMLHttpRequest` / `sendBeacon` / mutating MCP tool name,
  and the page has no `<form>`/`<button>`/`<input>` control.
- **Seam + packaging:** `poller.js` is a static asset (not in `SEAM_MODULES`); `render.py`
  gained only the inline-embed + poll-interval inject + initial-state JSON (still pure, no
  `mcp`/`starlette`). `test_adapter_seam.py` green. Verified `uv build --wheel` ships
  `dev_helper_mcp/dashboard/static/poller.js` so `importlib.resources` resolves it after
  `uv tool install`.
- **Gate (manual, run): GREEN** — `ruff check` ✓, `ruff format --check` ✓, `pytest -m "not
  slow"` ✓ (242 passed, 5 slow deselected), `node --test tests/js/` ✓ (16 passed). NO
  `.githooks/pre-commit` edit (test enforcement intentionally off; the hook runs only ruff).

### File List

- **NEW** `src/dev_helper_mcp/dashboard/static/poller.js` — the vanilla-ES poller
  (`diff`/`contentHash`/`worktreeKey` pure core + `patch`/`renderCard`/poll loop DOM layer).
- **NEW** `tests/js/diff.test.mjs` — `node --test` for the pure core (AR-12).
- **NEW** `tests/js/patch_spy.test.mjs` — dep-free zero-DOM-write spy (Decision D).
- **NEW** `tests/js/patch_dom.test.mjs` — dep-free DOM-stub test driving the real `patch`/`reparent`/`renderCard` (code-review follow-up; no jsdom — Decision D preserved).
- **NEW** `tests/test_dashboard_poller.py` — AC1 live-update + AC4 served-page read-only.
- **UPDATE** `src/dev_helper_mcp/config.py` — `DASHBOARD_POLL_INTERVAL_MS = 1500`.
- **UPDATE** `src/dev_helper_mcp/dashboard/render.py` — inline `poller.js`, inject
  `data-poll-interval`, embed initial `/state` JSON; Decision-B contract-sibling comment.
- **UPDATE** `tests/test_dashboard_static_lint.py` — split the timer test (allow
  `setTimeout`, forbid raf/setInterval); add poller-source GET-only/no-motion/no-external
  lints + page no-mutating-control + initial-state/poll-interval embed assertions.

### Completion Notes List (code review follow-up — 2026-06-26)

Adversarial code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) → 1 decision-needed + 5 patches applied, 1 deferred, ~10 dismissed. No AC violations; the content-hash exclusion of `description`/`orphaned` was **ratified** by the Acceptance Auditor as correct for 2.4b scope. Fixes applied:
- **poll loop now validates the response** — `if (!r.ok) throw` + skip/keep-DOM unless `Array.isArray(next.tasks)`, so a 503 `{"detail":"server not ready"}` (lifespan-teardown window) or any non-snapshot body no longer wipes the board + poisons `prev`.
- **unknown/`null` status now removes the node** — `reparent` computes the container first and `el.remove()`s when there's no home (matches a server reload, which renders only the four known statuses); no more re-shaped ghost cards.
- **poll interval clamped** — `Math.max(250, …)` guards a mis-set negative/tiny `DASHBOARD_POLL_INTERVAL_MS` from a tight poll loop.
- **lint hardened** — GET-only check now a case/quote-insensitive `method:` regex; `<script src>` check anchored (no bare `src=` substring); read-only-control greps run over script-stripped markup (no false-match on inlined JS/JSON).
- **DOM patch-layer now tested** — `tests/js/patch_dom.test.mjs` drives the real `patch`/`reparent`/`renderCard` against a dependency-free DOM stub (asserts node-identity-preserving reparent, sorted slot, count sync, done-morph, and the unknown-status removal) — no jsdom, Decision D intact. `poller.js` export extended to `{diff, contentHash, worktreeKey, patch, renderCard}` (node-only guard; browser unaffected).
- **Deferred:** `rebuildWorktrees` end-append will misorder once 2.4c adds a trailer after `.wt` — logged in `deferred-work.md` for 2.4c.
- Gate re-run GREEN: ruff ✓, `pytest -m "not slow"` ✓ (242), `node --test tests/js/` ✓ (now **24** — +8 DOM-layer tests).

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-26 | Code review (3 adversarial layers) → status `review` → `done`. Applied 5 patches: poll-loop `r.ok`/`Array.isArray(tasks)` guard (no board-wipe on a non-snapshot `/state`); unknown/`null`-status node removal in `reparent` (no ghost cards); poll-interval clamp `Math.max(250,…)`; tightened static-lint (GET-only `method:` regex, anchored `<script src>`, script-stripped control greps). Added `tests/js/patch_dom.test.mjs` (dep-free DOM-stub test of the real `patch`/`reparent`, no jsdom — resolves the decision-needed coverage gap); extended `poller.js` node export with `patch`/`renderCard`. 1 item deferred to 2.4c (`rebuildWorktrees` ordering). Content-hash field exclusion ratified (no AC violation). Gate green: ruff + 242 pytest + 24 node. |
| 2026-06-26 | Story 2.4b implemented (status → review). NEW `dashboard/static/poller.js` (pure exported `diff`/`contentHash`/`worktreeKey`; `patch`/`renderCard` DOM layer; `setTimeout` re-arm poll loop, no overlap) inlined into the page by `render_board` (+ `data-poll-interval`, + embedded initial `/state` JSON). Content hash covers only 2.4a-rendered fields (status + worktree repo/branch/path), excluding `generated_at`/`description`/`orphaned` to avoid needless DOM churn (2.4c extends it). Reparent-not-recreate on status change incl. column→done-disclosure morph; disclosures/scroll preserved. Tests: `tests/js/diff.test.mjs` (16 assertions incl. `diff(x,x)===[]`) + `tests/js/patch_spy.test.mjs` (dep-free 0-write spy); `tests/test_dashboard_poller.py` (AC1 live-update + AC4 read-only); extended `test_dashboard_static_lint.py`. `config.py` +`DASHBOARD_POLL_INTERVAL_MS`. Manual gate green (ruff + 242 pytest + 16 node). No core/store/projection/cache/routes/tokens change; no `.githooks/pre-commit` edit; wheel ships the static asset. |
| 2026-06-25 | Story 2.4b drafted (ready-for-dev): vanilla-JS live poller — `dashboard/static/poller.js` (pure exported `diff`/`contentHash`, `patch`, `renderCard`, setTimeout poll loop) inlined into the page; diff-and-patch keyed by `task_id` + content hash (excludes `generated_at`), reparent-not-recreate on status change, disclosure/scroll preserved; `node --test` for `diff(x,x)===[]` as part of the **manual** gate; dep-free spy for the 0-mutations check; live-update + read-only tests. Hard prerequisite: 2.1/2.2/2.3/2.4a implemented first. Decisions operator-confirmed: A poll=1500ms, B `renderCard` mirrors 2.4a markup, C `node --test` in the manual gate (node v20; **not** wired into the hook), D **dep-free spy** (no jsdom). Gotchas flagged: exclude `generated_at` from the hash, reparent node-identity, no overlapping polls, and that pre-commit test enforcement is intentionally off (gate is manual). |
