# Story 2.4c: Freshness, degraded, and empty states

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want the board to stay honest when data is stale, git is down, or there's nothing to show,
so that I'm never misled by a blank or silently-behind dashboard.

## Acceptance Criteria

1. **Given** the freshness stamp,
   **When** the snapshot age exceeds **2 × the poll interval**,
   **Then** `generated_at` (rendered small/cornered) shows the stale treatment (grey→amber); under the threshold it does not. **(UX-DR6)**

2. **Given** done tasks and orphaned annotations,
   **When** the page renders,
   **Then** the `✓ N done` and orphan sections are each a collapsed-by-default `<details>` below the board, self-explaining, never auto-expanded. **(UX-DR7)**

3. **Given** a git-unavailable refresh or a single slow/timed-out repo,
   **When** the page renders,
   **Then** it shows labeled last-known data with an explicit "stale — git unavailable" marker (never a blank board), and a single slow repo degrades only its own worktree lines ("unavailable") while other repos render normally. **(UX-DR8)**

4. **Given** empty states,
   **When** the page renders,
   **Then** an empty column shows header + "0"; the **empty Blocked column reads "Nothing needs you"**; a fully empty board shows "No active tasks — create one with `create_task`"; a zero-done state omits the done-disclosure. **(UX-DR9)**

## ⛔ HARD PREREQUISITE — read before anything else

**Story 2.4c cannot be implemented until Stories 2.1, 2.2, 2.3, 2.4a, and 2.4b are implemented.** It extends the rendered board (2.4a) and the live poller (2.4b) with the honest-when-degraded behaviors, reading the warnings/`orphaned` flags the 2.1 projection + 2.2 cache surface and 2.3 serves.

- 2.4a (`ready-for-dev`) ships `render_board(snapshot)->str` + the markup contract + `tokens.py` + the done-disclosure structure + the `.fresh[data-generated-at]` stamp.
- 2.4b (`ready-for-dev`) ships `dashboard/static/poller.js` (the poll loop + `diff`/`patch`) and `DASHBOARD_POLL_INTERVAL_MS`; freshness updates ride the poll.
- **The degrade signals in the payload (consumed, not produced here):** per-worktree `orphaned: true` (2.1) → orphan disclosure; `warnings` entries `orphan_link:<task_id>@<repo>:<branch>` (2.1) and `repo_unavailable:<repo_path>` (2.2) → orphan list + per-repo "unavailable" lines; an **aging `generated_at`** when the cache cannot swap on a total-git-failure (2.2 Decision B keeps last-known and lets `generated_at` age) → the stale treatment.
- If those stories are not implemented, implement them first, then return here.

## Tasks / Subtasks

- [ ] **Task 1 — Freshness threshold tunable + the stale rule** (AC: 1)
  - [ ] `config.py`: derive the stale threshold from the poll interval — `DASHBOARD_STALE_FACTOR = 2` (UX-DR6 "2 × poll interval"); the effective threshold is `DASHBOARD_POLL_INTERVAL_MS * DASHBOARD_STALE_FACTOR` (~3000ms). Comment it referencing UX-DR6.
  - [ ] **The freshness/stale calc is CLIENT-SIDE JS** (it is time-relative and must update between polls without new data): a pure `staleness(generatedAtIso, nowMs, thresholdMs) -> {stale: bool, label: string}` in `poller.js`. `stale` when `nowMs - parse(generatedAtIso) > thresholdMs`. Label = relative age ("updated 1s ago" / "updated 7s ago"). **Pure + exported** for `node --test` (Decision A: client-side, node-tested — matches the browser-free strategy by unit-testing the time logic, not the DOM).
  - [ ] The poller updates the `.fresh` stamp on every poll tick (and ideally on a lightweight timer between polls so the age keeps counting up — but **no style-mutating `requestAnimationFrame`**; a `setInterval` that only rewrites the stamp text/`data-stale` attr is data, not animation, and is UX-DR4-safe; keep it to the one stamp element). Toggle a `stale` class: grey under threshold, grey→amber over (DESIGN.md:109 `freshness-stamp`).
- [ ] **Task 2 — Stale / git-unavailable board treatment (AC: 3)** — *never blank*
  - [ ] **Whole-board stale:** when `staleness(...).stale` is true, render an explicit **"stale — git unavailable"** marker near the freshness stamp (UX-DR8) AND keep showing the last-known board (the snapshot in hand) — **never blank**. The board content is whatever `/state` last returned (2.2 keeps last-known on total git failure and ages `generated_at`, so the client infers staleness from age). The marker copy is exactly the UX-DR8 intent: last-known + "stale — git unavailable".
  - [ ] **Per-repo degrade:** parse `warnings` for `repo_unavailable:<repo_path>` entries; for each, render **only that repo's `worktree-line`(s)** as **"unavailable / last-known"** (e.g. append a muted `· unavailable` marker or a `data-unavailable` attr + styling) while **all other repos render normally** (UX-DR8; 2.2 Decision B). A single slow repo never blanks or fails the board.
  - [ ] This is BOTH a server-render concern (initial `render_board` must branch on `warnings`/age) AND a client concern (the poller re-applies it each tick via `patch`). Implement the **server render** branch (stdlib-parser-testable) and have the poller's `patch`/`renderCard` honor the same `warnings`/`repo_unavailable` logic (so a degrade that appears mid-session is patched in). Keep the parsing in one shared shape — a small `warningsIndex(warnings)` helper in JS, mirrored by a Python helper for the server render.
- [ ] **Task 3 — Orphan disclosure (AC: 2)** — *demoted, collapsed, self-explaining*
  - [ ] Extend `render_board` (2.4a) to populate the **orphan-disclosure**: a `<details class="fold">` BELOW the done-disclosure, **collapsed by default (no `open` attr)**, summary states the count (e.g. `1 orphaned annotation`), body lists each orphan as `<div class="o"><b>{branch}</b> — branch gone from git, note preserved here</div>` (DESIGN.md:108; EXPERIENCE.md:45; mock:109-111). Source the orphans from per-worktree `orphaned: true` AND/OR the `orphan_link:` warnings (use the warnings list — it carries `<task_id>@<repo>:<branch>` — as the authoritative orphan enumeration).
  - [ ] **Both Done and orphan `<details>` are collapsed-by-default and NEVER auto-expanded** even when non-empty (UX-DR7; EXPERIENCE.md:45). The poller must preserve their open-state across polls (already guaranteed by 2.4b's diff-and-patch — do not toggle `open`).
  - [ ] **Self-explaining summaries:** done = `✓ N done`; orphan = the plain count + (on expand) the "branch gone from git, note preserved here" line. No marketing, lowercase fragments (EXPERIENCE.md:37, 45).
- [ ] **Task 4 — Empty states (AC: 4)** — *honest absence*
  - [ ] **Empty column:** header shows the label + "0" and quiet empty space (no placeholder card) — UX-DR9. (2.4a already renders the header count; 2.4c adds the empty-column copy where required.)
  - [ ] **Empty Blocked column** specifically reads **"Nothing needs you"** (the only column with affirmative empty copy — confirming "I'm clear" is a feature; EXPERIENCE.md:37, 64).
  - [ ] **Fully empty board** (no tasks anywhere): a brief plain line **"No active tasks — create one with `create_task`"** (informative, not decorative). Note the backtick/code styling on `create_task`.
  - [ ] **Zero done:** OMIT the done-disclosure entirely (no `<details>` when N=0) — UX-DR9/UX-DR13. (Contrast with 2.4a, which renders the disclosure when there ARE done tasks; 2.4c adds the zero-case omission.) Similarly, a zero-orphan state omits the orphan-disclosure.
  - [ ] These are **server-render** branches (stdlib-parser-testable) AND mirrored in the poller so an emptying board shows the right copy live.
- [ ] **Task 5 — Tests** (AC: 1, 2, 3, 4)
  - [ ] **Freshness (UX-DR6) — `node --test` over `staleness(...)`:** age below `2×interval` → `{stale:false}`; above → `{stale:true}` + an amber label; boundary exactly at threshold (define inclusive/exclusive). Plus an optional server-render assertion: `render_board` with an old `generated_at` + an injected `now` (inject `now` for determinism, like 2.1's `generated_at`) emits the stale class; under → absent. (Decision B: if the server also renders an initial stale class, inject `now`; otherwise freshness is purely client/node-tested.)
  - [ ] **Orphan + Done disclosures (UX-DR7) — HTML-output (stdlib parser, no `selectolax`):** given a payload with done tasks + `orphan_link:` warnings, both `<details>` exist below the board, **neither has `open`**, the orphan body has the self-explaining line, summaries show correct counts. Given zero done → no done `<details>`; zero orphan → no orphan `<details>` (AC4 overlap).
  - [ ] **Degrade (UX-DR8) — HTML-output:** given a payload whose `warnings` include `repo_unavailable:/path/repoB`, the rendered board shows repoB's worktree line(s) marked "unavailable" while repoA's render normally (assert by parsing the specific `.wt`/`data-unavailable` nodes). Given a stale age (old `generated_at` + injected `now`), the "stale — git unavailable" marker text is present and the board is **not blank** (cards still rendered).
  - [ ] **Empty states (UX-DR9) — HTML-output, one assert per copy string:** empty Blocked → "Nothing needs you"; empty Running/Review column → header + "0", no placeholder card; fully empty board → "No active tasks — create one with `create_task`"; zero done → no done-disclosure. Assert each exact copy string for the corresponding empty payload.
  - [ ] **Static-lint stays green (UX-DR4/10):** the new stamp-updating `setInterval` rewrites only the freshness text/attr — assert no `requestAnimationFrame`, no CSS `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth`, no external asset (extend 2.4a/2.4b's `test_dashboard_static_lint.py`). The stale color shift is a class swap, not a CSS transition.
  - [ ] **Disclosure open-state preserved across a degrade/empty poll (UX-DR5/7 interplay):** a dep-free spy test (per 2.4b Decision D) that a poll which only changes freshness/warnings does NOT invoke `patch` on the disclosures / does NOT collapse an opened Done/orphan `<details>`.
- [ ] **Task 6 — Gate green + seam confirmation** (AC: all)
  - [ ] `render.py` gains degrade/empty/orphan branches (still pure, no `mcp`/`starlette`); `poller.js` gains `staleness`/`warningsIndex`/stamp-update; `config.py` gains `DASHBOARD_STALE_FACTOR`; core unchanged → `tests/test_adapter_seam.py` green.
  - [ ] Full gate (manual): `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` **and** `node --test tests/js/` (now also covering `staleness`). **No new dependency** (stdlib HTML parsing; dep-free spy — no jsdom). No schema change, no git command, no `.githooks/pre-commit` edit. ⚠️ Run the gate yourself.

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
2.4c is the **final UI story**: it makes the board honest when data is stale, git is down/slow, or there's nothing to show. It is the last layer over 2.4a (render) + 2.4b (poll), consuming the degrade signals 2.1/2.2 already surface. **It builds NO new server endpoint, NO new git path, NO cache change** — it interprets what `/state` already carries.

- **BUILD:** the freshness/stale calc (`staleness` in `poller.js`, `DASHBOARD_STALE_FACTOR` in config), the stale/git-unavailable board marker + per-repo "unavailable" lines, the orphan-disclosure population, the empty-state copy + zero-done/zero-orphan omission — across `render_board` (server) + `poller.js` (client), plus the HTML-output/node/lint tests.
- **DO NOT BUILD (out of scope / earlier-owned — hard fence):**
  - **No cache/projection/`/state` change** — the `repo_unavailable:`/`orphan_link:` warnings, `orphaned` flags, and last-known/aging-`generated_at` behavior are PRODUCED by 2.1 (projection) and 2.2 (cache Decision B). 2.4c only **reads** them. Do NOT add a server-side staleness field to the snapshot (2.1 shape is frozen); infer staleness from `generated_at` age client-side.
  - **No new orphan cleanup / reconciliation** — derive-on-read reports; cleanup is a v1 non-goal (architecture.md:439-443; Invariant 4). The orphan-disclosure is display-only; it never deletes.
  - **No change to the markup contract or the diff-and-patch engine** (2.4a/2.4b own them) — extend `render_board`'s branches and `poller.js`'s helpers; do not rewrite the patch loop.
  - **No motion** — the stale color shift is a class swap, not a transition; the stamp's ticking age is a text rewrite, not an animation.
- [Source: epics.md:452-474 (this story); epics.md:404-451 (2.4a/2.4b own structure+poll); architecture.md:439-443 (no reconciliation); 2.2 Decision B (degrade model).]

### ✅ Decision A — freshness is client-side JS, node-tested (OPERATOR-CONFIRMED 2026-06-25)
Staleness is **time-relative to the browser clock** and must update as the snapshot ages *between* polls (the page must shift to "stale" even if `/state` stops responding). **DECIDED:** the calc lives in `poller.js` as a pure `staleness(generatedAtIso, nowMs, thresholdMs)`, unit-tested via `node --test` (the browser-free strategy applied to time logic). The server emits the raw `generated_at` + `data-generated-at` (2.4a already does); the client computes + displays staleness.
- *Rejected as sole mechanism: server-only stale class* — the server is almost always fresh at serve time and cannot keep aging the stamp without a poll, so it would never show "stale" on a hung server. (The server MAY also render an initial stale class for a stale-at-load payload by injecting `now` — Decision B — but the live behavior is client-side.)

### ✅ Decision B — degrade/empty/orphan server-rendered (stdlib-HTML-parser-testable); freshness client (OPERATOR-CONFIRMED 2026-06-25)
UX-DR7/8/9 are tested via **HTML-output assertions** (EXPERIENCE.md:102; parsed with the **stdlib HTML parser** per 2.4a Decision C — no `selectolax`), so the **orphan disclosure, per-repo "unavailable" lines, empty-state copy, and zero-done/orphan omission MUST be in the Python `render_board`** (parse the served HTML for them). UX-DR6 (freshness threshold) is tested via the **`node --test` `staleness` function** (time logic); the server also renders an initial stale class for a stale-at-load payload by **injecting `now`** (deterministic, like 2.1's `generated_at` injection) so the initial stale class has an HTML-output assertion too. Split: degrade/empty/orphan = server (stdlib-parsed); live freshness = client (node).

### What 2.1/2.2 hand you (the degrade contract — read it, don't reinvent it)
- **`orphaned: true`** per `WorktreeView` (2.1) — a link whose branch is absent from its repo's porcelain. Surfaced, never auto-deleted/auto-`done`. → orphan disclosure + (the worktree may also still appear if you choose; the spec's orphan UI lists the annotation). [2.1 AC2; architecture.md:344-348]
- **`warnings: ["orphan_link:<task_id>@<repo>:<branch>", …]`** (2.1) — the authoritative orphan enumeration for the disclosure. [2.1 AC2]
- **`warnings: ["repo_unavailable:<repo_path>", …]`** (2.2 Decision B) — a repo whose read failed this tick; its worktrees are **carried-forward last-known**, NOT flipped to orphaned. → render that repo's lines "unavailable / last-known", NOT in the orphan disclosure. **Distinguish `repo_unavailable:` (transient/slow) from `orphan_link:` (branch genuinely gone)** — they get different UI (unavailable line vs orphan disclosure). [2.2 Decision B; AC3]
- **Aging `generated_at` on total git failure** (2.2 Decision B) — the cache keeps last-known and does NOT swap, so `generated_at` ages → the client crosses the stale threshold → "stale — git unavailable". A **partial** degrade DOES swap (fresh `generated_at` + `repo_unavailable:` warnings). So: whole-board stale = old `generated_at`; per-repo degrade = fresh stamp + `repo_unavailable:` warning. [2.2 Decision B; AC3]
- **`done` tasks** carry `status="done"` and stay in the snapshot (2.1/2.2) → folded into the done-disclosure; zero done → omit it. [2.4a; 2.1 closed-semantics]

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Invariant 4 — derive-on-read; surface, never auto-clean.** The orphan disclosure DISPLAYS orphans; it never deletes or reconciles (cleanup is a v1 non-goal). The per-repo "unavailable" is a transient display state, not a mutation. [architecture.md:68, 344-348, 439-443]
- **Invariant 3 — snake_case** — the JS/renderer read `generated_at`, `warnings`, `repo_path`, `orphaned` directly. [architecture.md:67]
- **Invariant 7 — SDK seam** — `render.py` stays pure; `poller.js` is a static asset; no core change. [architecture.md:71]
- **No motion (UX-DR4)** — stale = class swap; ticking age = text rewrite to ONE element; no `requestAnimationFrame`, no CSS transition. [DESIGN.md:66, 109, 116]
- **Never blank (UX-DR8)** — a stale/git-down board shows last-known + a label; a blank board "reads as no work, which is a lie" (EXPERIENCE.md:60). [epics.md:468-470]
- **Read-only (FR-10)** — still no mutating control; the orphan/degrade UI is display-only. [epics.md:441]

### Critical gotchas (carry into implementation)
- **⚠️ The gate is a manual command; pre-commit test enforcement was intentionally removed — do not restore it.** `.githooks/pre-commit` runs only `ruff`. Run `uv run pytest -m "not slow"` and `node --test tests/js/` **yourself**; do not add a pytest/node step to the hook (operator decision 2026-06-25).
- **Distinguish `repo_unavailable:` from `orphan_link:`.** They look similar (both warnings) but mean opposite things: `repo_unavailable` = transient/slow read, render the repo's lines "unavailable/last-known" (it will recover); `orphan_link` = branch genuinely gone, list in the orphan disclosure. Mixing them either hides a real orphan or alarms on a slow repo. (2.2 Decision B is explicit: carry-forward, not orphan, on transient failure.)
- **Don't add a `stale` field to the snapshot.** 2.1's shape is frozen; staleness is INFERRED from `generated_at` age client-side. Adding a field would fork the contract and break 2.1/2.2/2.3 tests.
- **Disclosures never auto-expand.** Even with non-empty orphans/done, both `<details>` render WITHOUT `open` (UX-DR7). And the poller must preserve a user-opened state across polls (2.4b guarantees this — don't toggle `open` in any 2.4c code path).
- **Zero-done OMITS the disclosure; non-zero RENDERS it collapsed.** Two different states — test both. Same for orphans.
- **Empty Blocked is special.** Only the Blocked column gets affirmative copy ("Nothing needs you"); empty Running/Review just show header+"0". Don't put "Nothing needs you" on every empty column.
- **The ticking-age timer touches ONE element.** If you add a `setInterval` to keep the "updated Ns ago" counting between polls, it must rewrite only the `.fresh` stamp's text + `data-stale`/class — never anything else, or the static-lint "no timer-driven style mutation" intent is violated. Keep it minimal; or update the stamp only on each poll tick (simpler, still ≤ poll-interval granularity — acceptable since the threshold is 2× the interval).
- **`generated_at` excluded from the task content hash (2.4b)** — so a pure freshness change does NOT churn task cards; the stamp updates independently. Confirm 2.4b honored this; 2.4c relies on it.

### 🛑 Git safety in tests — HARD RULE
2.4c's render/empty/degrade/orphan tests build **payload dicts by hand** (an `orphan_link:` warning, a `repo_unavailable:` warning, an old `generated_at`, an empty `tasks` list) — **zero git surface**, like 2.1/2.4a. The `staleness` node test is pure JS. Any integration test that seeds real degrade via git must use `tmp_git_repo` (autouse guard + `test_git_safety.py` enforce it) — but prefer hand-built payloads (faster, deterministic, no git). (project-context.md#Git safety in tests.)

### Previous-story intelligence that applies directly
- **2.2 Decision B is the degrade contract** — read it before implementing Task 2/3. `repo_unavailable:` (carry-forward last-known) vs `orphan_link:` (genuinely gone) vs aging-`generated_at` (whole-board stale) are the three distinct signals → three distinct UI treatments. [2.2 Decision B]
- **2.4a renders the happy path + done-disclosure-when-nonempty** — 2.4c adds the zero-case omission + empty copy + orphan disclosure + degrade lines. Extend `render_board`'s branches; don't rewrite it. [2.4a scope fence]
- **2.4b excludes `generated_at` from the content hash + preserves disclosures/scroll** — 2.4c's freshness updates ride on top without churning cards or collapsing disclosures. [2.4b gotchas]
- **"This file wins over architecture pseudo-code."** The degrade/empty/freshness copy + thresholds are pinned in EXPERIENCE.md (the binding UX spec) — follow its exact strings ("Nothing needs you", "No active tasks — create one with `create_task`", "branch gone from git, note preserved here", "2 × poll interval"). [project-context.md#Usage Guidelines; EXPERIENCE.md:37, 45, 60, 64]
- **Test style proven 1.1–2.4b:** synchronous HTML-output tests over `render_board(payload)`; `node --test` for the pure JS time logic; in-process ASGI only where a live path is needed. No `pytest-asyncio`. [project-context.md#Testing rules]

### Git / recent-work intelligence
- **Baseline `cc6c8fe` ("1-6 complete").** Epic 2 prior: 2.1/2.2/2.3/2.4a/2.4b drafted `ready-for-dev`. 2.4c is the closing story of Epic 2; after it, `epic-2` can move toward `done` and a retrospective.
- **Commit cadence:** one commit per story after a green gate + adversarial review. Files: UPDATE `dashboard/render.py` (degrade/empty/orphan/stale branches), `dashboard/static/poller.js` (+`staleness`/`warningsIndex`/stamp-update), `config.py` (+`DASHBOARD_STALE_FACTOR`), tests; possibly `tests/js/staleness.test.mjs`. No core/store/projection/cache/`/state` change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100; `from __future__ import annotations`. **node v20.19.4** for the `staleness` `node --test`.
- **No new runtime dep.** Date parsing in JS: `Date.parse(generatedAtIso)` on the `now_iso()` UTC-`Z` format (util.now_iso → `2026-06-22T11:00:00Z`) — standard `Date.parse` handles the `Z` suffix. (In `node --test`, pass `nowMs` explicitly for determinism — do NOT call `Date.now()` inside `staleness`; inject it, mirroring 2.1's injected-clock discipline.)
- **No schema change, no git command, no new endpoint.**

### Project Structure Notes
- **UPDATE:** `src/dev_helper_mcp/dashboard/render.py` (orphan-disclosure population, per-repo "unavailable" lines, stale "git unavailable" marker for a stale-at-load payload, empty-state copy, zero-done/zero-orphan omission), `src/dev_helper_mcp/dashboard/static/poller.js` (+ pure `staleness`, `warningsIndex`, freshness-stamp update, degrade re-apply in `patch`/`renderCard`), `src/dev_helper_mcp/config.py` (+`DASHBOARD_STALE_FACTOR`).
- **NEW:** `tests/js/staleness.test.mjs` (node --test for the freshness fn); extend `tests/test_dashboard_render.py`/`test_dashboard_static_lint.py` (or add `tests/test_dashboard_states.py`) for the degrade/empty/orphan HTML-output asserts.
- **UNCHANGED (do not edit):** `dashboard/routes.py` (2.3/2.4a), `dashboard/tokens.py` (2.4a — unless an `amber`/stale token is genuinely missing; the `blocked` amber already exists, reuse it), `cache.py`, `projection.py` (frozen), `store.py`, all `core/`, `git/`, `middleware.py`, `errors.py`, `util.py`, `tools/`, `server_factory.py`. **DB schema unchanged.**
- **DEFERRED / out of scope:** any orphan cleanup/reconciliation (v1 non-goal), any new endpoint, any cache/snapshot-shape change. Epic 2 ends here. [architecture.md:439-443; epics.md:452-474]
- Test mirrors src: `tests/test_dashboard_*.py` + `tests/js/staleness.test.mjs`. Architecture's planned `test_cache.py` already notes per-repo degrade at the cache layer (2.2); 2.4c covers its UI surfacing.

### Testing standards
- **HTML-output (stdlib parser, no `selectolax`) over `render_board(payload)`** for UX-DR7/8/9 (orphan/done disclosures collapsed; per-repo "unavailable"; empty copy strings; zero-done/orphan omission) — synchronous, hand-built payloads, no git.
- **`node --test` over `staleness(...)`** for UX-DR6 (below/above/at threshold; amber label). Pure, inject `nowMs`.
- **Static-lint** extended: no `requestAnimationFrame`/`transition`/`animation`/`@keyframes`, no external asset, the stamp timer (if any) touches only the freshness element.
- **Disclosure-preservation** (dep-free spy, per 2.4b Decision D): a freshness/warnings-only poll does not collapse an opened `<details>`.
- **Coverage to the four ACs:** (1) stale at >2×interval, not under (node `staleness`); (2) Done + orphan `<details>` below board, collapsed, never-`open`, self-explaining (HTML); (3) per-repo "unavailable" line for `repo_unavailable:` + whole-board "stale — git unavailable" + never blank (HTML); (4) empty column header+"0", empty Blocked "Nothing needs you", empty board "No active tasks — create one with `create_task`", zero done omits disclosure (HTML, one assert per copy).
- Green under the **manual** gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` **and** `node --test tests/js/`. `tests/test_adapter_seam.py` green. **Run the gate yourself — pre-commit test enforcement is intentionally off.**

### References
- [Source: epics.md:452-474] — Story 2.4c user story + all 4 BDD ACs verbatim (stale at >2×interval; Done+orphan collapsed disclosures never auto-expanded; git-unavailable last-known + "stale" marker + per-repo "unavailable"; empty-state copy incl. "Nothing needs you" / "No active tasks…" / zero-done omit). Maps to UX-DR6/7/8/9.
- [Source: epics.md:92-95] — UX-DR6/7/8/9 ownership = 2.4c; UX-DR8 explicitly pairs with Story 2.2.
- [Source: ux-designs/.../EXPERIENCE.md:48-66] — State Patterns: freshness/staleness (>2×poll-interval, grey→amber), git-unavailable last-known never-blank, per-repo degradation, empty states (exact copy: "Nothing needs you", "No active tasks — create one with `create_task`").
- [Source: ux-designs/.../EXPERIENCE.md:30-31, 44-46] — done-disclosure + orphan-disclosure behavior (collapsed by default, never auto-expand, self-explaining "branch gone from git, note preserved here").
- [Source: ux-designs/.../EXPERIENCE.md:109-112] — UX-DR6/7/8/9 machine-checkable predicates (stale class threshold; both `<details>` lack `open`; degraded `/state` label + other repos normal; each empty copy string).
- [Source: ux-designs/.../DESIGN.md:107-109] — done-disclosure / orphan-disclosure / freshness-stamp tokens + the grey→amber stale shift.
- [Source: ux-designs/.../mockups/key-screen-board.html:101-111] — the folded Done + orphan `<details>` markup (`.fold`, `✓ N done`, the orphan `.o` line).
- [Source: architecture.md:344-348, 439-443] — orphaned links surfaced, NEVER auto-cleaned; crash-safety/reconciliation are v1 non-goals (the orphan UI displays, never deletes).
- [Source: architecture.md:359-360] — cache keeps last-known + marks stale on git-unavailable (the source of the aging-`generated_at` signal).
- [Source: 2-2-in-memory-cache-and-background-refresher.md (Decision B)] — `repo_unavailable:<repo>` warnings + carry-forward last-known + don't-swap-on-total-fail (aging `generated_at`) — THE degrade contract 2.4c renders. Distinguish from `orphan_link:`.
- [Source: 2-1-derive-on-read-projection-pure-task-grouped.md] — `orphaned: true` + `orphan_link:` warnings + `done` tasks present in the snapshot.
- [Source: 2-4a-…/2-4b-….md] — the markup contract + done-disclosure structure + the freshness stamp + the poller/diff-patch (excludes `generated_at` from the hash; preserves disclosures) that 2.4c extends.
- [Source: src/dev_helper_mcp/util.py:6-14] — `now_iso()` UTC `Z` format that `Date.parse` consumes client-side.
- [Source: project-context.md] — derive-on-read/no-auto-clean, snake_case, SDK seam, no-motion, never-blank, read-only, testing rules, git-safety, the quality gate (and its real state), "this file wins over architecture pseudo-code".

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-25 | Story 2.4c drafted (ready-for-dev): freshness/degraded/empty states — client-side `staleness()` (node-tested) + stale "git unavailable" marker (never blank); per-repo "unavailable" lines from `repo_unavailable:` warnings (distinct from `orphan_link:` orphans); orphan-disclosure (collapsed, never auto-expanded, self-explaining); empty-state copy ("Nothing needs you", "No active tasks — create one with `create_task`") + zero-done/orphan disclosure omission. Consumes 2.1/2.2 degrade signals; produces no new endpoint/cache change. Hard prerequisite: 2.1–2.4b implemented first. Decisions operator-confirmed: A freshness client-side + node-tested, B degrade/empty/orphan server-rendered (**stdlib HTML parser**) + freshness client. Gotchas flagged: repo_unavailable vs orphan_link distinction, no `stale` field on the frozen snapshot, disclosures never auto-expand, and that pre-commit test enforcement is intentionally off (gate is manual). |
