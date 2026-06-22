# Story 2.4a: Static board structure + status encoding

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want the dashboard to render a fixed `/state` payload as a glanceable, accessible board,
so that I can read every task's status by position and shape, not color alone.

## Acceptance Criteria

1. **3 active columns + folded Done + summary bar (UX-DR1, UX-DR2, UX-DR13).**
   **Given** a fixed `/state` payload,
   **When** the page renders,
   **Then** the board has **exactly three active columns** — Running | Blocked | Review (lifecycle order) — grouped **by task** (one card per task, per-repo worktrees nested as `repo · branch` lines); `done` is NOT a column but a collapsed `✓ N done` `<details>` below the board; a **summary count bar** shows one pill per status (incl. done) whose counts equal the rendered columns/disclosure, zero-counts shown.

2. **Per-card non-color encoding + blocked emphasis (UX-DR3, UX-DR4-emphasis).**
   **Given** any task card,
   **When** it renders,
   **Then** status is encoded by **column + colored left bar + a per-card glyph (●/▲/◆/✓) + a `data-status` attribute** — never color alone; **blocked** is the only lifted card (running flat, done dimmed); reason badges read "needs input" (blocked) / "awaiting review" (review) and the markup contains no "merge" string.

3. **No motion + self-contained + WCAG AA contrast (UX-DR4, UX-DR10, UX-DR11).**
   **Given** the rendered page and its assets,
   **When** inspected,
   **Then** there is no `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth` in the CSS and no `requestAnimationFrame`/timer-driven style mutation in the JS (no motion, `prefers-reduced-motion` safe); all CSS/JS is inline with a system font stack and no external `src`/`href`/`@import`/`http(s)://` references (self-contained); and each enumerated status text/bar token pair meets WCAG AA against `{bg}`/`{surface}` (text ≥4.5:1, non-text ≥3:1) by a pure contrast-ratio check.

4. **Overflow contract (UX-DR12).**
   **Given** the board container,
   **When** an active column overflows,
   **Then** it scrolls within the column and the board never scrolls horizontally (3→1 wrap at narrow width).

## Tasks / Subtasks

- [ ] **Task 1 — `dashboard/static/index.html` + server render of the board (AC: 1, 2)**
  - [ ] Render the board **server-side from a `CacheSnapshot`** (so HTML-output tests are browser-free): summary bar → 3-column grid (Running | Blocked | Review) → `done-disclosure` `<details>` → `orphan-disclosure` `<details>`
  - [ ] **Group by task:** one `task-card` per `TaskView`, with N nested `worktree-line`s (`repo · branch`, full path on `title` hover) — never N cards for N repos
  - [ ] Each card: title row = `status-bar` (left stripe) + per-card **glyph** + task name + optional `reason-badge`; `data-status="running|blocked|review|done"` attribute on the card node
  - [ ] Glyphs: **running ● · blocked ▲ · review ◆ · done ✓** — on every card, not just the header
  - [ ] `done` tasks render ONLY inside the `done-disclosure` (`✓ N done` summary), never in a column
  - [ ] Summary bar: one `status-pill` per status incl. done (glyph + count + label); counts equal the rendered columns/disclosure; **zero-count pills still render** (e.g. "0 blocked")
  - [ ] Reason badges: blocked → "needs input"; review → "awaiting review". **The markup must contain no "merge" string anywhere.**
- [ ] **Task 2 — inline CSS with the DESIGN tokens (AC: 2, 3, 4)**
  - [ ] Inline `<style>` (no external stylesheet); system font stack only; **no external `src`/`href`/`@import`/`http(s)://`**
  - [ ] **No motion:** no `transition`, `animation`, `@keyframes`, `scroll-behavior:smooth`
  - [ ] Status color applied to: card left **bar**, per-card **glyph**, column **header**, summary **pill**. **Blocked is the only lifted card** (tinted ring + amber bloom); running flat; review flat (blue bar); done dimmed (`opacity:~.55`) inside its disclosure
  - [ ] Layout: single board `max-width:1000px` centered, page padding `16px 18px 26px`; summary bar above a `grid-template-columns:repeat(3,1fr)` (gap 10px, `align-items:start`); cards `card-gap:8px`, padding `9px 11px 9px 14px`; left status bar 3px clipped to card radius (`overflow:hidden`); rounded card 8px, pill 999px, badge 5px
  - [ ] **Overflow:** board `overflow-x` none/hidden (never horizontal); a column `overflow-y:auto`; 3→1 wrap at narrow width
- [ ] **Task 3 — render function shared with the poller seam (AC: 1, 2)**
  - [ ] Factor the snapshot→card markup so Story 2.4b's diff-and-patch JS can produce the **identical** node shape (same `data-status`, glyph, keys by `task_id`). Keep card identity keyed by `task_id`
- [ ] **Task 4 — tests (browser-free, under AR-12 gate)**
  - [ ] HTML-output asserts (HTML parser, e.g. `selectolax`): exactly 3 active columns + a `<details>` done section (UX-DR1); summary pills parsed, counts == column/done counts, zero-counts present (UX-DR2); every task node has a glyph + `data-status`, no "merge" string (UX-DR3); done tasks appear only inside the `<details>` (UX-DR13)
  - [ ] Static CSS/JS lint (grep): no `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth`; no external `src`/`href`/`@import`/`http(s)://`/`//` (UX-DR4, UX-DR10)
  - [ ] Pure WCAG contrast-ratio function over the enumerated token pairs: each status text + bar/glyph vs `{bg}` and `{surface}` ≥4.5:1 text / ≥3:1 non-text (UX-DR11)
  - [ ] Overflow: board container `overflow-x` none/hidden, column `overflow-y:auto` (UX-DR12)

## Dev Notes

### Scope boundaries — read first
Renders a **fixed/static `/state` payload** into the board markup + CSS. **OUT of scope:** the live poller and diff-and-patch (Story 2.4b); freshness-stale / git-unavailable / per-repo-degrade / empty-state copy (Story 2.4c — render the happy path here; 2.4c layers the edge states onto this structure). Build the structure so 2.4b/2.4c slot in without rework (card identity by `task_id`, `data-status` attribute, disclosure nodes present).

### Render strategy (reconciles "server-rendered HTML" + "browser-free tests")
The architecture specifies **minimal server-rendered HTML + a tiny vanilla-JS poller** and a **browser-free** test strategy (HTML-output assertions in pytest). So render the board **server-side from the `CacheSnapshot`** on `GET /` — pytest asserts against that HTML string with an HTML parser (no Playwright). 2.4b's JS poller then takes over for live updates, producing the identical node shape. Keep the snapshot→markup mapping in one place so server-render and JS-render cannot drift. [Source: architecture.md#Frontend Architecture; EXPERIENCE.md#Foundation, #Design Requirements (test strategy)]

### Exact DESIGN tokens (embedded — DESIGN.md is the binding visual contract)
**Colors:** bg `#0e1117` · surface `#161b22` · border `#21262d` · text `#e6edf3` · text-muted `#8b949e` · text-dim `#586069` (freshness) · worktree-repo `#c9d1d9` · bar-done `#373e47`.
**Status palette (load-bearing):** running `#39d0a8` (teal), running-border `#1f3f37` · blocked `#e3a34a` (amber), blocked-bg `#2a2113`, blocked-border `#3d3320` · review `#6cb6ff` (blue), review-bg `#16263d`, review-border `#1f3a5c` · done `#7d8590` (grey, left bar uses `#373e47`).
**Glyphs:** running ● · blocked ▲ · review ◆ · done ✓ (on every card).
**Type:** sans `ui-sans-serif, system-ui, sans-serif` for chrome/titles; mono `ui-monospace, 'SF Mono', Menlo, monospace` for repo/branch/path lines. Sizes: task-title 13px/650, column-header 11.5px/700 (.05em tracking, uppercase), worktree 11.5px, badge 10.5px, freshness 11.5px.
**Shape/space:** card 8px, pill 999px, badge 5px; status bar 3px; page padding `16px 18px 26px`; card padding `9px 11px 9px 14px`; card-gap 8px; column-gap 10px; board `max-width:1000px` centered.
**Blocked elevation (the only lift):** `box-shadow: 0 0 0 1px #3d3320, 0 2px 12px rgba(227,163,74,.14)`; cards otherwise carry 1px `#21262d` border + `0 1px 2px rgba(0,0,0,.3)`. Done cards drop to `opacity:~.55`. [Source: DESIGN.md — colors/typography/spacing front-matter, #Colors, #Elevation & Depth, #Shapes]

### Behavioral/IA rules (EXPERIENCE.md is the binding behavioral contract)
- **Grouping is by task**; per-repo worktrees nest. A task with N repos = one card with N worktree lines. [Source: EXPERIENCE.md#Information Architecture]
- **Urgency gradient (visual weight):** blocked → review → running → done. Blocked is the alarm (lifted); running is the calm flat baseline; review flat blue; done folded away. [Source: EXPERIENCE.md#State Patterns; DESIGN.md#Elevation & Depth]
- **Voice:** terse, factual; labels are nouns/short verbs; lowercase reason fragments; **never the word "merge"**; numbers over prose. [Source: EXPERIENCE.md#Voice and Tone]
- **Accessibility floor:** never color-only — column position + left bar + per-card glyph + `data-status`; the glyph travels with the card (teal/blue bars are close for deuteranopia). [Source: EXPERIENCE.md#Accessibility Floor]

### Builds on Stories 2.1 + 2.3 (previous-story intelligence)
- Consumes the `CacheSnapshot` shape from 2.1 (TaskView.status drives the column/glyph; WorktreeView gives `repo · branch`/path). Match the snake_case field names exactly (the JS in 2.4b reads them too).
- Served via the `dashboard/` routes from 2.3 (`GET /`); reuses the read-only, self-contained, Origin-guarded surface. No new mutating route.
- `dashboard/` is presentation layer — it may touch `starlette` but no git/DB. Core seam unaffected.

### Source tree components to touch
`dashboard/static/index.html`, `dashboard/static/style.css` (or inline in the template), the snapshot→HTML render (in `dashboard/routes.py` or a small render helper), `dashboard/static/app.js` (skeleton only — full poller is 2.4b); tests as above (e.g. `test_dashboard_render.py` + the contrast-math test). Add `selectolax` to dev deps if not present. [Source: architecture.md#Complete Project Directory Structure — dashboard/static]

### Project Structure Notes
- All assets shipped **inside the package** (`dashboard/static/`), served by Starlette; **no external assets / no egress** (UX-DR10, NFR-Security/Locality). [Source: architecture.md#File Organization Patterns; EXPERIENCE.md#Foundation]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 2.4a: Static board structure + status encoding] — acceptance criteria + UX-DR mapping
- [Source: epics.md#UX Design Requirements] UX-DR1/2/3/4/10/11/12/13
- [Source: DESIGN.md] — full token set, colors, elevation, shapes (the binding visual contract)
- [Source: EXPERIENCE.md] — IA, state patterns, voice, accessibility floor, UX-DR predicates (the binding behavioral contract)
- [Source: architecture.md#Frontend Architecture] — server-rendered HTML + vanilla-JS poller, no SPA/build

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
