# Story 2.4a: Static board structure + status encoding

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want the dashboard to render a fixed `/state` payload as a glanceable, accessible board,
so that I can read every task's status by position and shape, not color alone.

## Acceptance Criteria

1. **Given** a fixed `/state` payload,
   **When** the page renders,
   **Then** the board has **exactly three active columns** — Running | Blocked | Review (lifecycle order) — grouped **by task** (one card per task, per-repo worktrees nested as `repo · branch` lines); `done` is NOT a column but a collapsed `✓ N done` `<details>` below the board; a **summary count bar** shows one pill per status (incl. done) whose counts equal the rendered columns/disclosure, zero-counts shown. **(UX-DR1, UX-DR2, UX-DR13)**

2. **Given** any task card,
   **When** it renders,
   **Then** status is encoded by **column + colored left bar + a per-card glyph (●/▲/◆/✓) + a `data-status` attribute** — never color alone; **blocked** is the only lifted card (running flat, done dimmed); reason badges read "needs input" (blocked) / "awaiting review" (review) and the markup contains no "merge" string. **(UX-DR3, UX-DR4-emphasis)**

3. **Given** the rendered page and its assets,
   **When** inspected,
   **Then** there is no `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth` in the CSS and no `requestAnimationFrame`/timer-driven style mutation in the JS (no motion, `prefers-reduced-motion` safe); all CSS/JS is inline with a system font stack and no external `src`/`href`/`@import`/`http(s)://` references (self-contained); and each enumerated status text/bar token pair meets WCAG AA against `{bg}`/`{surface}` (text ≥4.5:1, non-text ≥3:1) by a pure contrast-ratio check. **(UX-DR4, UX-DR10, UX-DR11)**

4. **Given** the board container,
   **When** an active column overflows,
   **Then** it scrolls within the column and the board never scrolls horizontally (3→1 wrap at narrow width). **(UX-DR12)**

## ⛔ HARD PREREQUISITE — read before anything else

**Story 2.4a cannot be implemented until Stories 2.1, 2.2, and 2.3 are implemented.** It renders the `/state` payload that 2.3 serves from the 2.2 cache over the 2.1 projection.

- 2.3 (`ready-for-dev`) ships `src/dev_helper_mcp/dashboard/{__init__.py, routes.py}` (the `/state` GET endpoint) and the **route-ordering pattern** in `server_factory.create_app` (`Route(...)` before the catch-all `Mount("/")`). 2.4a adds the board page at `/` using the **same ordering trick**.
- The board renders a `CacheSnapshot` payload (snake_case): `{generated_at, tasks:[{task_id, description, status, created_at, updated_at, worktrees:[{repo_path, branch, path, head, detached, locked, prunable, orphaned}]}], warnings:[…]}` (2.1 shape; 2.3 serializes it via `dataclasses.asdict`).
- If `dashboard/routes.py` does not exist, implement 2.1 → 2.2 → 2.3 first, then return here.
- **This story renders a FIXED payload** (initial server-side render). The live `/state` *poll loop* and diff-and-patch are **Story 2.4b**; freshness/stale/degraded/empty-state behavior is **Story 2.4c**.

## Tasks / Subtasks

- [ ] **Task 1 — Pin the dashboard tokens in one place** (AC: 3) — *prevents CSS↔contrast-test drift*
  - [ ] `src/dev_helper_mcp/dashboard/tokens.py` (NEW): the DESIGN.md color tokens as a Python dict/constants (single source of truth) — `BG="#0e1117"`, `SURFACE="#161b22"`, `BORDER="#21262d"`, `BAR_DONE="#373e47"`, `TEXT="#e6edf3"`, `TEXT_MUTED="#8b949e"`, `TEXT_DIM="#586069"`, `WORKTREE_REPO="#c9d1d9"`, `RUNNING="#39d0a8"`, `RUNNING_BORDER="#1f3f37"`, `BLOCKED="#e3a34a"`, `BLOCKED_BG="#2a2113"`, `BLOCKED_BORDER="#3d3320"`, `REVIEW="#6cb6ff"`, `REVIEW_BG="#16263d"`, `REVIEW_BORDER="#1f3a5c"`, `DONE="#7d8590"`. Also the **glyph map** `STATUS_GLYPH = {"running":"●","blocked":"▲","review":"◆","done":"✓"}` and the **status order** `ACTIVE_COLUMNS = ("running","blocked","review")`. (DESIGN.md frontmatter `colors:` is the authority; copy verbatim.) Pure module — no SDK, no I/O.
  - [ ] The renderer injects these into the `:root{ --bg:…; … }` CSS block; the **contrast test imports the same module** — so the tested colors ARE the served colors (no drift). [Decision B]
- [ ] **Task 2 — Build the pure server-side renderer `dashboard/render.py`** (AC: 1, 2, 4) — *the heart of the story* [Decision A: server-side render]
  - [ ] `src/dev_helper_mcp/dashboard/render.py` (NEW): `def render_board(snapshot: dict) -> str` — a **pure** function: takes the snake_case snapshot dict (the `asdict(CacheSnapshot)` shape), returns a complete HTML page **string**. No `mcp`/`starlette` import, no I/O, no clock read — total and deterministic (mirror 2.1's purity discipline; makes it unit-testable without a server). Accept the dict shape (not the dataclass) so tests build payloads as plain dicts.
  - [ ] **Page skeleton:** the harness wraps your output in `<!doctype html>…<head>…</head><body>…` — but this is a *standalone* server-served page, so produce the **full** document yourself: `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" …><title>dev-helper-mcp</title><style>…inline…</style></head><body>…board…<script>…</script></body></html>`. (No external `<link>`/`<script src>` — UX-DR10.)
  - [ ] **Inline `<style>`** built from `tokens.py` `:root` vars + the component CSS adapted from the reference mock (`mockups/key-screen-board.html:2-62`). Reuse the mock's class system: `.summary/.pill/.cols/.col/.col-h/.card/.t/.g/.badge/.wt/.fold/.donecard`. **No `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth`** anywhere (UX-DR4). System font stack only (`ui-sans-serif, system-ui, sans-serif`; mono `ui-monospace,…`) — no `@font-face`, no web font (UX-DR10).
  - [ ] **Summary bar (UX-DR2):** one `pill` per status in order `running, blocked, review, done`, each `<glyph> <count> <label>` (e.g. `● 3 running`); **counts computed from `snapshot["tasks"]` grouped by `status`**; **zero-count pills still render** ("0 blocked"). The blocked pill is bolder (weight 700). A right-aligned `freshness-stamp` placeholder showing `generated_at` (raw for now — live relative-age + stale treatment is **2.4c**; just emit the value + a `data-generated-at` attribute the 2.4c JS will read). Done count in the bar **equals** the done-disclosure count (UX-DR13).
  - [ ] **Board (UX-DR1):** a `<div class="cols">` grid with **exactly three** `<div class="col col-{running|blocked|review}">`, each with a `col-h` header `"<glyph> <Label> <count>"` (lifecycle order Running | Blocked | Review). Each column lists one **task-card per task** whose `status` matches the column. `done` tasks go to the disclosure, NOT a column (UX-DR13).
  - [ ] **task-card (UX-DR3):** `<div class="card {status}" data-status="{status}" data-task-id="{task_id}">` (the `data-task-id` is for 2.4b's diff key — emit it now). Title row `<div class="t"><span class="g">{glyph}</span>{task_id or description}{optional reason-badge}</div>`. The **per-card glyph travels on the card** (not just the column header) — color-blind channel. Then one `worktree-line` per repo: `<div class="wt"><span class="repo">{repo basename or path}</span> · {branch}</div>` (full path on `title=` hover). Sort cards/worktrees per the snapshot (already sorted by 2.1: tasks by `task_id`, worktrees by `repo_path`).
  - [ ] **reason-badge (UX-DR3):** blocked → `<span class="badge b">needs input</span>`; review → `<span class="badge r">awaiting review</span>`; running/done → no badge. **The markup must contain no "merge" string anywhere** (review ≠ merge — DESIGN.md:118-119, EXPERIENCE.md:37). Grep-tested.
  - [ ] **Emphasis (UX-DR4):** blocked card is the only lifted element (ring + amber bloom via the `.card.blk` box-shadow from the mock); running flat; review flat; done dimmed (opacity ~.55 inside the disclosure). Encoded in CSS, no motion.
  - [ ] **done-disclosure (UX-DR1/13):** a `<details class="fold done">` (collapsed, **no `open` attr**) below the board, `<summary>✓ {N} done</summary>` expanding to dimmed `donecard` rows (each with the ✓ glyph). (The orphan-disclosure population is **2.4c**; for 2.4a you MAY emit the empty done-disclosure structure — but the *zero-done omits the disclosure* rule is 2.4c. Keep 2.4a's scope to: done-disclosure present when there ARE done tasks; see scope fence.)
  - [ ] **Escaping:** HTML-escape every interpolated value (`task_id`, `description`, `repo_path`, `branch`) — `markupsafe`/`html.escape`. A repo path or description is operator-controlled but still must not break the markup or inject a tag. Use stdlib `html.escape` (no new dep).
- [ ] **Task 3 — Overflow CSS (UX-DR12)** (AC: 4)
  - [ ] Board container `.cols`: `overflow-x: hidden` (or none) — the board **never** scrolls horizontally. Each `.col`: `overflow-y: auto` with a `max-height` (e.g. `calc(100vh - <summary-bar+padding>)`) so an over-full column scrolls **within itself**. (The mock omits these — 2.4a ADDS them.)
  - [ ] **3→1 wrap at narrow width** via a media query: `@media (max-width: 680px){ .cols{ grid-template-columns: 1fr; } }` (value tunable). A media query is static layout, not motion — UX-DR4-safe.
- [ ] **Task 4 — Serve the board at `/`** (AC: 1)
  - [ ] In `dashboard/routes.py` add `def board_route(holder) -> Route:` returning `Route("/", board, methods=["GET"])`. Handler: read `deps = holder.deps`; if `None` → a minimal "server not ready" HTML (or 503) consistent with 2.3's Decision A; else `html = render_board(dataclasses.asdict(deps.cache.current))` → `return HTMLResponse(html)`.
  - [ ] In `server_factory.create_app`, add `board_route(holder)` to `routes=[…]` **before `Mount("/", app=mcp_app)`** (same ordering trick 2.3 established). Order: `[state_route(holder), board_route(holder), Mount("/")]`. **`/` must resolve to the board, `/mcp` still to the MCP app with no 307** — verify `test_server_factory.py` stays green.
  - [ ] **Read-only (FR-10):** `/` is GET-only; the page contains **no form, no button, no fetch-POST, no control** that mutates. (The poll loop is 2.4b and is GET-only too.)
- [ ] **Task 5 — Tests** (AC: 1, 2, 3, 4)
  - [ ] **`tests/test_dashboard_render.py` — HTML-output assertions (browser-free, via the stdlib HTML parser — Decision C, no new dep).** Use stdlib `html.parser.HTMLParser`/`xml.etree` or a regex-light parse. Build fixed snapshot dicts and assert against `render_board(...)` output:
    - **(AC1/UX-DR1/13):** exactly 3 active columns (`.col-run/.col-blk/.col-rev` or `data` markers), grouped by task (one `.card[data-task-id]` per task; a 2-repo task → ONE card with TWO `.wt` lines), `done` tasks appear ONLY inside the `<details class="fold done">`, never in a column.
    - **(AC1/UX-DR2):** one summary pill per status incl. done; pill counts == rendered column card counts and == done-disclosure count; a zero-status still renders its "0 …" pill.
    - **(AC2/UX-DR3):** every `.card` has a `data-status` ∈ {running,blocked,review,done} AND a per-card glyph matching `STATUS_GLYPH`; blocked card has `needs input`, review has `awaiting review`; **no occurrence of the substring "merge"** in the whole document (case-insensitive).
    - **(AC2/UX-DR4-emphasis):** the blocked card carries the lift class; running/review are flat; done is dimmed (assert the class/opacity rule presence).
  - [ ] **`tests/test_dashboard_static_lint.py` — static CSS/JS lint (grep, UX-DR4 + UX-DR10).** Over the rendered HTML: assert **absent**: `transition`, `animation`, `@keyframes`, `scroll-behavior:smooth`, `requestAnimationFrame`; assert **no external assets**: no `http://`/`https://`/protocol-relative `//`, no `<link …href>`, no `<script …src>`, no `@import`, no `url(http…)`. (2.4a ships no JS yet, or a tiny no-op — the JS lint mainly bites in 2.4b/c; include the grep now so it guards the page from the start.)
  - [ ] **`tests/test_dashboard_contrast.py` — pure WCAG-ratio math (UX-DR11).** A pure `contrast_ratio(hex_fg, hex_bg) -> float` (relative-luminance per WCAG 2.x; no dep). Import the tokens from `dashboard/tokens.py`. Assert the **enumerated pairs** (EXPERIENCE.md:82 — *each status text + bar/glyph against bg and surface*): for `status ∈ {running, blocked, review, done}`: `contrast(status_color, BG) ≥ {4.5 for text-use, 3.0 for bar/glyph}` and same against `SURFACE`; plus primary `TEXT` vs `BG`/`SURFACE` ≥ 4.5. **If a pair fails** (e.g. `DONE` text on `SURFACE` is a known borderline ~4.3:1; dimmed-done is non-text), do NOT silently lower the threshold — **escalate as a finding** (adjust the token in `tokens.py` toward AA, or, if the pair is genuinely non-text/decorative, document why it is tested at 3.0 not 4.5). Record the resolution in the Change Log. (UX-DR11 is "no adjective without a threshold/ratio" — keep the thresholds honest.)
  - [ ] **`tests/test_dashboard_overflow.py` (or fold into render test) — UX-DR12.** Parse the inline CSS: `.cols` has `overflow-x` none/hidden (no horizontal board scroll); a `.col` has `overflow-y:auto`; a max-width media query collapses the grid to one column.
  - [ ] **Read-only smoke (FR-10):** `GET /` over the in-process ASGI client → 200 `text/html`; the document has no `<form>`, no `<button>`, no mutating control. (Wrap in `async with app.router.lifespan_context(app):`; base URL `http://127.0.0.1:<port>`.)
- [ ] **Task 6 — Gate green + seam confirmation** (AC: all)
  - [ ] `dashboard/{tokens,render,routes}.py` are adapter (`render`/`tokens` are pure but live under the adapter `dashboard/` package — not in `SEAM_MODULES`); core stays unchanged → `tests/test_adapter_seam.py` green.
  - [ ] Full gate (manual): `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. **No new dependency** (stdlib HTML parsing — Decision C). No schema change, no git command, no JS test yet (the `node --test` for the poller `diff()` is **2.4b**). ⚠️ Run the suite yourself — there is no pytest pre-commit enforcement (intentional; see gotcha).

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
2.4a is the **first UI story** of the 2.4 split: a **static, server-side-rendered** board for a **fixed** `/state` payload, glanceable and accessible. It establishes the markup/CSS contract the poller (2.4b) patches and the edge-states (2.4c) extend.

- **BUILD:** `dashboard/tokens.py` (colors/glyphs/order), `dashboard/render.py` (pure `render_board(snapshot)->str`), the `/` board route in `dashboard/routes.py`, the route wiring in `server_factory.py`, and the HTML-output/static-lint/contrast/overflow tests.
- **DO NOT BUILD (later stories — hard fence):**
  - **No `/state` poll loop, no `fetch`, no `setInterval`, no diff-and-patch, no JS render** → **Story 2.4b**. 2.4a's page is fully formed by the **server**; a `<script>` is optional/no-op here. The page must be correct with JS disabled.
  - **No live freshness/relative-age, no stale (grey→amber) treatment, no "git unavailable" label, no per-repo "unavailable" lines, no orphan-disclosure population, no empty-state copy ("Nothing needs you" / "No active tasks…"), no zero-done-omits-disclosure rule** → **Story 2.4c**. 2.4a emits the raw `generated_at` + `data-generated-at` and renders the happy-path board; interpreting warnings/edge states is 2.4c.
  - **No change to `/state` (2.3), `cache.py`/`projection.py` (2.1/2.2)** — 2.4a only *reads* the snapshot shape.
  - **No mutating control of any kind** (FR-10) — read-only forever.
- [Source: epics.md:404-427 (this story); epics.md:428-451 (2.4b poller); epics.md:452-474 (2.4c edge states); DESIGN.md; EXPERIENCE.md.]

### ✅ Decision A — server-side Python rendering (OPERATOR-CONFIRMED 2026-06-25)
The **test strategy is browser-free** and the named technique is "**HTML-output assertions** (an HTML parser, e.g. `selectolax`)" (EXPERIENCE.md:102; AR-12; UX-DR1/2/3 tests "parse pills", "HTML has exactly 3 columns"). That is only possible if the **board HTML is produced server-side in Python** — a pytest renders `render_board(payload)` (or `GET /`) and parses the output. So:
- **DECIDED:** `render_board(snapshot: dict) -> str` builds the **complete** board HTML in Python; `/` serves it; tests parse the output with an HTML parser. The page is correct before any JS runs (progressive enhancement). 2.4b then adds a JS poller that **patches this server-rendered DOM** (and, for tasks appearing mid-session, builds matching card nodes — a small structure duplication, pinned by this story's markup contract).
- *Rejected: client-only JS render* (server serves an empty shell + JS builds the board from `/state`) — makes UX-DR1/2/3 untestable without a browser/jsdom, contradicting the browser-free mandate.
- **Implication for 2.4b:** the card markup (classes, `data-status`, `data-task-id`, glyph, `.wt` lines) is a **cross-story contract** — 2.4b's JS must create nodes with the same structure so diff-and-patch is coherent. This story OWNS that markup shape.

### Decision B — tokens in one Python module (DRY, drift-proof)
Colors live in `dashboard/tokens.py`; the renderer injects them into `:root`, and the **contrast test imports the same module** — so the tested ratios are over the *served* colors, impossible to drift. (Alternative: parse `:root` vars out of the rendered HTML in the test — also valid; the module is simpler and reusable by 2.4b/c.) DESIGN.md frontmatter `colors:` is the source values; copy them verbatim into `tokens.py`.

### ✅ Decision C — HTML-parser for tests (OPERATOR-CONFIRMED 2026-06-25: stdlib, no new dep)
EXPERIENCE.md names `selectolax` *as an example*; it is **not** currently a dev dep, and the project holds a **minimal-dependency posture** (project-context.md:25).
- **DECIDED: parse with the stdlib** (`html.parser.HTMLParser` or `xml.etree.ElementTree` after a light normalize, or targeted regex for attributes/classes) — **no new dependency**, fits the minimal posture. The assertions (count columns, find `data-status`/glyph, count pills, grep "merge") are simple enough for stdlib.
- *Rejected:* adding `selectolax` as a dev dep — ergonomic but unnecessary; keep the dependency footprint minimal.

### The render contract (markup the whole 2.4 arc depends on)
Adapted from the reference mock (`mockups/key-screen-board.html`) — **the mock's CSS/markup is essentially the implementation**; reproduce its class system and add the missing overflow + the `data-*` hooks:
```
<div class="summary"> <span class="pill {status}"><span class="g">{glyph}</span>{n} {label}</span>… <span class="fresh" data-generated-at="{generated_at}">{generated_at}</span> </div>
<div class="cols">
  <div class="col col-run"><div class="col-h">● Running <span class="n">{n}</span></div>
     <div class="card run" data-status="running" data-task-id="{id}"><div class="t"><span class="g">●</span>{id}</div>
        <div class="wt"><span class="repo">{repo}</span> · {branch}</div>…</div>…</div>
  <div class="col col-blk">…▲ Blocked… card.blk + badge.b "needs input"…</div>
  <div class="col col-rev">…◆ Review… card.rev + badge.r "awaiting review"…</div>
</div>
<details class="fold done"><summary>✓ {n} done</summary><div class="body"><div class="donecard">…</div>…</div></details>
```
- **Glyphs:** running ●, blocked ▲, review ◆, done ✓ — on **every** card title row, not just the header (DESIGN.md:115; EXPERIENCE.md:81 color-blind channel).
- **`data-status` + `data-task-id`** on each card: `data-status` is the UX-DR3 non-color token AND 2.4b's reparent target; `data-task-id` is 2.4b's diff key. Emit both now even though the poll is 2.4b.
- **Grouping by task:** ONE card per task with N `.wt` lines — never N cards (EXPERIENCE.md:33). The snapshot is pre-grouped/sorted by 2.1; render in that order.

### What the substrate already gives you (consumed contracts)
- **`/state` payload = `dataclasses.asdict(CacheSnapshot)`** (2.3) — snake_case `{generated_at, tasks:[{task_id, description, status, created_at, updated_at, worktrees:[{repo_path, branch, path, head, detached, locked, prunable, orphaned}]}], warnings}`. `render_board` consumes this dict shape; tests build it by hand. [2.1 shape; 2.3 serialize]
- **Route-ordering pattern** (2.3, server_factory.py) — add `board_route(holder)` to `routes` before `Mount("/")`. Same `_DepsHolder`. [2.3 Task 4]
- **Reference mock** `mockups/key-screen-board.html` — committed visual direction; its `<style>` (lines 2-62) and markup (64-112) are the template. Reproduce classes; ADD overflow (Task 3) + `data-*` hooks; do not pull its caption `<p class="cap">` (that is mock annotation, not product chrome).
- **DESIGN.md** (visual identity; tokens, components, do/don'ts) and **EXPERIENCE.md** (IA, voice, state patterns, the authoritative UX-DR1–13 with predicates) — both "win over any mock". Read both fully before rendering.

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 3 — snake_case keys** — the renderer reads snake_case payload keys directly; JS (2.4b) will too (no translation layer). [architecture.md:67]
- **Invariant 7 — SDK seam** — `render.py`/`tokens.py` are pure (no `mcp`/`starlette`); the route in `routes.py` is adapter. Core stays SDK-free. [architecture.md:71]
- **NFR-Security/Locality + NFR-Simplicity — self-contained page**: inline CSS/JS, system fonts, **no external assets / no network egress** (UX-DR10). The page must load offline on localhost. [architecture.md:408-416; EXPERIENCE.md:19]
- **Read-only (FR-10)** — the dashboard renders state; it never mutates. No control creates/edits/removes anything. [epics.md:441; EXPERIENCE.md:20]
- **No motion (UX-DR4)** — nothing animates, ever; satisfies `prefers-reduced-motion` by construction. [DESIGN.md:66, 116; EXPERIENCE.md:76]

### Critical gotchas (carry into implementation)
- **⚠️ The test suite is NOT auto-run on commit (intentional — do not restore it).** The pre-commit `pytest` enforcement was **purposefully removed for now** (operator decision 2026-06-25); the hook runs only `ruff`. The gate is a **manual command** in v1 — run `uv run pytest -m "not slow"` and `uv run ruff format --check .` yourself; do not re-add the pytest line to `.githooks/pre-commit`.
- **Route order: `/` before `Mount("/")`.** Same trap as 2.3 — list `board_route` before the catch-all Mount or `/` is shadowed by the MCP app. `/mcp` must still 200 with no 307.
- **Escape interpolated values.** `repo_path`/`description`/`task_id` flow into HTML — `html.escape` them. (Slugs are constrained, but descriptions and paths are freer.)
- **Done is NEVER a column.** Three columns only (Running | Blocked | Review). Done → the folded `<details>`. The summary bar still shows the done **count** (UX-DR13). A test asserts no done card sits in `.cols`.
- **Zero-count pills render.** "0 blocked" must appear — absence must be legible, not a missing pill (UX-DR2; EXPERIENCE.md:41). (The empty-*column* copy like "Nothing needs you" is 2.4c — don't add it here.)
- **The contrast test will likely flag a borderline pair.** `done` (#7d8590) text on `surface` (#161b22) computes ~4.3:1 (< 4.5 text AA); the glyph/bar use is non-text (≥3:1, passes). Resolve honestly: done text inside the disclosure is dimmed/decorative → test it as non-text (3:1), OR nudge the token. Do not weaken the 4.5 threshold for genuine text. Document the call.
- **No "merge" anywhere.** Review badge is "awaiting review"; the grep test fails on any "merge" substring (DESIGN.md:118-119).
- **The `<script>` is optional in 2.4a and must be no-motion.** If you include a stub, it must not use `requestAnimationFrame`/timers that mutate style (UX-DR4) and must be inline (UX-DR10). The real poller is 2.4b.
- **Overflow: ADD what the mock omits.** The mock has no `overflow`/media query; UX-DR12 requires `.cols{overflow-x:hidden}` + `.col{overflow-y:auto}` + a 3→1 wrap media query. Don't ship the mock verbatim.

### 🛑 Git safety in tests — HARD RULE (2.4a spawns essentially no git)
`render_board` is pure (no git, no I/O); its tests build payload dicts by hand — **zero git surface**, like 2.1. The only git is an optional `GET /` smoke that, if it seeds a task, must use `tmp_git_repo` (never the project repo; autouse `_guard_project_repo_untouched` + `test_git_safety.py` enforce it). Prefer testing `render_board(fixed_dict)` directly — no server, no git. (project-context.md#Git safety in tests.)

### Previous-story (2.1/2.2/2.3) intelligence that applies directly
- **`done` tasks are in the snapshot** (status `"done"`); the renderer **folds** them into the disclosure — it does NOT filter them out upstream (that is by design — 2.1/2.2 surface everything; the UI decides visibility). [2.1 Dev Notes#Two coexisting closed semantics]
- **`orphaned: true` worktrees and `warnings` are present in the payload** but their *rendering* (orphan disclosure, "unavailable" lines) is **2.4c**. 2.4a renders the happy path; do not crash on an orphaned/warning-bearing payload (be total), just don't build the orphan UI yet.
- **Purity discipline** (2.1) — `render_board` takes plain data, returns a string, no clock/IO; deterministic for tests. Mirror it.
- **Test style proven 1.1–2.3:** plain `pytest`, in-process ASGI for the `GET /` smoke (lifespan-wrapped, `http://127.0.0.1:<port>`), no `pytest-asyncio`. The render-output tests are **synchronous** (pure function). [project-context.md#Testing rules]

### Git / recent-work intelligence
- **Baseline `cc6c8fe` ("1-6 complete").** Epic 2 prior: 2.1/2.2/2.3 drafted `ready-for-dev`. `dashboard/` package created by 2.3 (`__init__.py`, `routes.py`); 2.4a adds `tokens.py`, `render.py`, the `/` route, and the test files.
- **Commit cadence:** one commit per story after a green gate + adversarial review. Files: NEW `dashboard/tokens.py`, `dashboard/render.py`, `tests/test_dashboard_render.py`, `tests/test_dashboard_static_lint.py`, `tests/test_dashboard_contrast.py` (+ overflow assertions); UPDATE `dashboard/routes.py` (+`board_route`), `server_factory.py` (route list). No core/store/projection/cache change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`; `from __future__ import annotations`. Type hints on every public signature (`render_board(snapshot: dict) -> str`).
- **stdlib `html.escape`** for escaping (no new dep). HTML-output parsing: stdlib (Decision C — no `selectolax`).
- **No new runtime dependency.** `HTMLResponse` from `starlette.responses` (already transitive). The contrast function is pure arithmetic — no `wcag`/color lib.
- **No JS test in this story.** `node --test` for the poller `diff()` arrives with **2.4b** (and wires the node harness into the gate then).

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/dashboard/tokens.py` (colors/glyphs/order), `src/dev_helper_mcp/dashboard/render.py` (pure `render_board`). `tests/test_dashboard_render.py`, `tests/test_dashboard_static_lint.py`, `tests/test_dashboard_contrast.py` (overflow may fold into render or its own file).
- **UPDATE:** `src/dev_helper_mcp/dashboard/routes.py` (+`board_route(holder)`), `src/dev_helper_mcp/server_factory.py` (add `board_route` to `routes` before `Mount("/")`).
- **UNCHANGED (do not edit):** `dashboard/routes.py`'s `/state` handler (2.3), `cache.py`, `projection.py`, `store.py`, all `core/`, `git/`, `middleware.py` (already covers `/`), `errors.py`, `util.py`, `tools/`. **DB schema unchanged.**
- **DEFERRED, do NOT create or pull forward:** the JS poll loop + diff-and-patch + `dashboard/static/poller.js` + `node --test` (2.4b); freshness/stale/degraded/per-repo-unavailable/orphan-disclosure/empty-state copy/zero-done-omit (2.4c). [epics.md:428-474]
- Test mirrors src: `tests/test_dashboard_*.py`. (Architecture's planned UI-test posture: HTML-output asserts + static lint + WCAG math — no Playwright. architecture.md:71/AR-12; EXPERIENCE.md:102.)

### Testing standards
- **Render-output tests are synchronous** over `render_board(fixed_dict)` — no server, no git. Parse with the stdlib (Decision C — no `selectolax`). The `GET /` smoke uses the in-process ASGI client (lifespan-wrapped, `http://127.0.0.1:<port>`).
- **Coverage to the four ACs:** (1) 3 columns + by-task grouping + done-in-disclosure-not-column + summary pills==counts + zero-pills; (2) `data-status` + per-card glyph on every card + badges + no "merge" + blocked-lifted/running-flat/done-dimmed; (3) static lint (no motion tokens, no external assets) + WCAG-AA contrast over enumerated pairs; (4) `overflow-x` none on board, `overflow-y:auto` on column, 3→1 wrap media query. Plus read-only smoke (no `<form>`/`<button>`).
- Green under the manual gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dependency (stdlib parsing — Decision C). `tests/test_adapter_seam.py` stays green. **Run the suite yourself — no pytest pre-commit enforcement (intentional).**

### References
- [Source: epics.md:404-427] — Story 2.4a user story + all 4 BDD ACs verbatim (3 columns + folded Done + summary bar; per-card non-color encoding + blocked emphasis + no "merge"; no-motion + self-contained + WCAG-AA; overflow contract). Maps to UX-DR1/2/3/4/10/11/12/13.
- [Source: epics.md:86-99] — UX-DR1–13 summary + which story owns each (2.4a owns 1/2/3/4/10/11/12/13).
- [Source: epics.md:428-474] — 2.4b (poller/diff-patch) + 2.4c (freshness/degrade/empty) scope fence — what 2.4a must NOT build.
- [Source: ux-designs/.../DESIGN.md] — visual identity: color tokens (frontmatter `colors:` → `tokens.py`), typography, layout/spacing, elevation (blocked-only lift), shapes (status bar + glyph), components, Do/Don'ts ("no merge", "don't animate", "done not a column", "glyph on every card").
- [Source: ux-designs/.../EXPERIENCE.md] — IA (summary bar → 3 columns → done-disclosure → orphan-disclosure), voice ("needs input"/"awaiting review", never "merge"), state patterns (4-status, urgency gradient), accessibility floor (never color-only; WCAG AA enumerated pairs at :82; glyph on every card), and the **authoritative UX-DR1–13 with machine-checkable predicates** (:104-116).
- [Source: ux-designs/.../mockups/key-screen-board.html] — reference mock: the `<style>` (2-62) and markup (64-112) template; reproduce classes, add overflow + `data-*`.
- [Source: architecture.md:362-391] — the `CacheSnapshot`/`TaskView`/`WorktreeView` shape the payload follows.
- [Source: architecture.md:768, 793, 826] — `dashboard/` is adapter (FR-8–10); `dashboard/static/` for shipped assets; the board route `/`.
- [Source: src/dev_helper_mcp/server_factory.py:188-220] — `create_app`/`_DepsHolder`/route-list (where `board_route` slots, before `Mount("/")`).
- [Source: 2-3-read-only-state-endpoint.md] — the `dashboard/` package, the `/state` payload (`asdict`), the route-ordering pattern, Decision A (deps-null window).
- [Source: 2-1-…projection….md / 2-2-…cache….md] — snapshot shape; `done` tasks + `orphaned`/`warnings` present in the payload (rendered in 2.4c).
- [Source: project-context.md] — SDK seam, snake_case, self-contained/no-egress, read-only, no-motion, minimal-deps posture, testing rules, git-safety, the enforced quality gate (and its current real state).

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-25 | Story 2.4a drafted (ready-for-dev): server-side-rendered static board — `dashboard/tokens.py` (colors/glyphs/order) + pure `dashboard/render.py` (`render_board(snapshot)->str`) + `/` board route; 3 active columns grouped by task, folded Done disclosure, summary pills, per-card glyph + `data-status` non-color encoding, blocked-only lift, no-motion/self-contained, overflow contract; browser-free tests (HTML-output, static lint, WCAG-contrast math, overflow). Hard prerequisite: 2.1/2.2/2.3 implemented first. Decisions operator-confirmed: A server-side render, B tokens in one module, C **stdlib HTML parser (no `selectolax`)**. Gotchas flagged: route ordering, the borderline `done`-on-surface contrast pair, and that pytest pre-commit enforcement is intentionally removed (gate is a manual command). |
