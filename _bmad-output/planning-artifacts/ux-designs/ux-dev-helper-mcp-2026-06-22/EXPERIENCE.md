---
title: dev-helper-mcp dashboard — Experience Spec
status: final
created: 2026-06-22
updated: 2026-06-22
sources:
  - ../../prds/prd-dev-helper-mcp-2026-06-19/prd.md
  - ../../architecture.md
  - ../../epics.md
---

# dev-helper-mcp dashboard — Experience Spec

> Owns *how it works*. Visual tokens live in [DESIGN.md](./DESIGN.md), referenced as `{token}`. Both spines win over any mock. Reference mock: `mockups/key-screen-board.html`.

## Foundation

- **Form-factor:** a single web page served by the local process, viewed in a **desktop browser tab on localhost** by one developer-operator. No mobile surface, no auth, no multi-user.
- **UI system:** none — minimal server-rendered HTML + a tiny vanilla-JS poller. No SPA, no build tooling, **no external assets** (the page must load offline on localhost; all CSS/JS/font stacks are system or inline). DESIGN.md is the visual identity reference.
- **Read-only:** the dashboard renders state; it never mutates. No control creates/edits/removes worktrees or tasks, and none launches agents (FR-10).
- **North star:** SM-2 — the operator can state what every agent is doing, and what needs them, in **under ~10 seconds** at a glance.

## Information Architecture

One screen, top to bottom:

1. **Summary bar** — one count pill per status (Running · Blocked · Review · Done) + a right-aligned freshness stamp. The whole-system tally for the instant "how many need me?" read. The blocked pill is the boldest.
2. **Board** — **three active columns** in lifecycle order: **Running | Blocked | Review**. Each column lists `task-card`s for tasks in that status; a count sits in the column header. `done` is *not* a column (see item 4).
3. **task-card** — task description/name + reason badge; nested `worktree-line`s, one per repo the task spans (`repo · branch`, path on hover).
4. **done-disclosure** — a collapsed `<details>` below the board, summary `✓ N done`, expanding to dimmed done cards. Done needs no action and accretes across a day, so it is kept out of the primary glance but one click away.
5. **orphan-disclosure** — a collapsed `<details>` below the done-disclosure listing annotations whose branch is gone from git. Out of the primary glance path.

Grouping is **by task**; per-repo worktrees nest inside their task. A task with N repos is one card with N worktree lines — never N cards.

## Voice and Tone

Terse, factual, operator-to-operator. Labels are nouns/short verbs (`Running`, `needs input`, `awaiting review`, `done`). **Never the word "merge"** — merge-back is out of scope, so "merge" language would promise a button that doesn't exist; review means "needs your eyes," nothing more. Reasons are lowercase fragments, not sentences. The orphan summary is plain and self-explaining: *"branch gone from git, note preserved here."* The empty blocked column says *"Nothing needs you"* — confirming "I'm clear" is a feature. No marketing, no emoji in chrome, no exclamation. Numbers over prose ("3 running" not "three tasks are running").

## Component Patterns (behavioral)

- **summary-bar** — counts recompute from the same snapshot as the board; never disagree with the columns (the done count agrees with the done-disclosure). Zero-count pills still render (show "0 blocked") so the absence is legible, not ambiguous.
- **task-card** — entire card is non-interactive (read-only) except the worktree path tooltip. Reason badge appears only when a reason exists (blocked/review); running/done need none. **blocked** cards are lifted (the alarm); running/review are flat.
- **worktree-line** — shows `repo · branch`; full worktree path on `title` hover (avoids truncation noise in the dense layout).
- **done-disclosure** — collapsed by default; summary `✓ N done`; expands to dimmed done cards (each with the ✓ glyph). The done count stays visible in the summary bar even when folded.
- **orphan-disclosure** — collapsed by default; the summary states the count; expanding lists `branch — branch gone from git, note preserved here`. Never auto-expands, even when non-empty.
- **freshness-stamp** — subordinate; states relative age ("updated 1s ago"). Proof of liveness, not a headline.

## State Patterns

**Task status (the 4-state set — canonical, mirrors FR-6):**
- **running** — agent actively working. The **calm flat baseline** (teal bar, no lift) — it needs nothing from the operator.
- **blocked** — agent awaiting the operator's **input**; stuck mid-work. Amber; **the only lifted/alarm state** (an idle agent is the most urgent thing); badge names the need ("needs input").
- **review** — agent **finished**, awaiting the operator's **review**. Blue (calm, waiting); badge "awaiting review". An *active* (non-done) state. *(Not "ready to merge" — review ≠ merge.)*
- **done** — reviewed/closed. Grey, dimmed; folded into the done-disclosure, not on the board. Terminal.

Urgency gradient (visual weight): **blocked → review → running → done**.

**Freshness / staleness:** the page polls `/state`; `generated_at` renders subordinate. When the snapshot is older than **2 × the poll interval** (~3–4s) the stamp greys further and shifts amber ("updated 7s ago") — signalling the data may be behind without alarming.

**git-unavailable / stale snapshot:** if a refresh fails (git unreachable), the board shows the **last-known state, clearly labeled stale** — never a blank board (a blank board reads as "no work," which is a lie).

**Per-repo degradation:** if one repo's `git worktree list` times out during refresh, **only that repo's worktree lines** render as "unavailable / last-known"; the rest of the board is normal. One slow repo never blanks or fails the whole view.

**Empty states:** an empty column shows its header with a "0" and quiet empty space (not a placeholder card). The **empty Blocked column reads "Nothing needs you"** — confirming "I'm clear" is a feature, not blank ambiguity. A fully empty board (no tasks anywhere) shows a brief, plain line ("No active tasks — create one with `create_task`.") — informative, not decorative. A zero-done state simply omits the done-disclosure.

**Overflow:** active columns are bounded by agent count (2–4 typical) and fit without scroll; a column that overflows scrolls **within the column**; the board never scrolls horizontally.

## Interaction Primitives

- **Polling:** vanilla JS fetches `/state` every ~1–2s. Target freshness ≤3s (bounded to ≤15 repos per the architecture SLO).
- **Stable render — diff-and-patch (mechanical contract):** the poller does **not** replace `innerHTML` wholesale. It holds the last snapshot and computes a per-`task_id` delta against the new one:
  - key absent in new → remove that node; key new → create + insert in sorted position;
  - key in both → compare a per-task content hash (status, slug, repos, reason, freshness); **equal → no DOM write at all**; changed → patch only the changed fields on the existing node;
  - a status change **reparents** the existing node to the new column (`insertBefore`), never destroy-and-recreate.
  This preserves the open/closed state of the Done/orphan disclosures and any scroll position across polls. Testable: identical consecutive `/state` → the `diff()` function returns an empty patch set (`diff(x,x) === []`), and a `MutationObserver` over the board records **zero** mutations.
- **No motion:** no transitions, spinners, or pulses anywhere (DESIGN.md). Status changes appear as a state swap, not an animation.
- **Disclosure:** the Done and orphan `<details>` are the only interactive controls — native toggle, keyboard-operable; their open/closed state survives polls (per the diff-and-patch contract).

## Accessibility Floor

- **Never color-only:** every status is encoded three more ways — column **position**, the left **bar**, and a **per-card glyph** (● running · ▲ blocked · ◆ review · ✓ done) **on every card** (not just the column header — the shape must travel with the card, since the bar's teal/blue can be close for deuteranopia). A fully colorblind operator reads the board by column + glyph.
- **Contrast:** status hues and text must meet WCAG **AA (≥4.5:1 text, ≥3:1 non-text** bar/glyph) against `{colors.bg}`/`{colors.surface}`. The token pairs under test: each of running/blocked/review/done text + bar against bg and card surface. *(Computed at build via a WCAG ratio check over the token set — see UX-DR11.)*
- **Motion:** none — `prefers-reduced-motion` is satisfied by construction.
- **Keyboard/SR:** the `<details>` disclosure is keyboard-operable; the board is a semantic list/region with the status as text, not conveyed by color alone.

## Key Flows

**Dev fans out three agents and glances at the board (realizes UJ-1 / SM-2).**
1. Dev has the dashboard tab open beside the editor; three agents are working across repoA/repoB/repoD.
2. Dev glances over. The lifted, amber **Blocked** card is the first thing the eye lands on — the alarm — then the blue **Review** card; running hums quietly in its calm column; the done count sits folded.
3. **Climax:** in under ten seconds Dev knows the whole picture without a terminal — `db-migration` is **blocked** ("needs input"), so it gets attention *first*; `payments-api` is **awaiting review** (finished, safe, can wait); the rest are running and need nothing. The urgency order is read straight off the visual weight.
4. Dev switches to the blocked agent, gives input; on the next poll the card silently reparents from Blocked to Running. No reload, no animation, and the Done section Dev had unfolded stays unfolded — the board just tells the truth.

## Responsive & Platform

Single desktop browser tab is the only target. The 3-column grid may wrap (3→1 column) below a narrow width (the page must never scroll horizontally); an over-full active column scrolls within itself. Mobile is out of scope. No print, no dark/light toggle — dark only.

## Design Requirements (handoff to epics)

Actionable UX-DRs to fold into Epic 2's stories (each testable):

Each carries a machine-checkable predicate (Murat's gate: no adjective without a threshold/selector/ratio). The test strategy is **browser-free** — HTML-output assertions (pytest), static CSS/JS lint (grep), a pure WCAG-contrast math check, and a `node --test` unit test for the poller `diff()` — **no Playwright/Cypress**.

- **UX-DR1 — 3 active columns + folded Done.** Render Running | Blocked | Review (lifecycle order), grouped by task, per-repo worktrees nested; `done` is a collapsed `✓ N done` disclosure below the board (not a column), expandable to dimmed done cards. *Test:* HTML has exactly 3 active columns + a `<details>` done section. *(FR-8; FR-6 4-status.)*
- **UX-DR2 — Summary count bar.** One count pill per status (incl. done) + subordinate freshness stamp; counts equal the rendered column/disclosure counts; zero-counts shown. *Test:* parse pills, assert == column/done counts. *(FR-8/9.)*
- **UX-DR3 — Per-card non-color encoding.** Each task node carries column + left bar + a **per-card glyph** (●/▲/◆/✓) + a `data-status` token; reason badges "needs input" (blocked) / "awaiting review" (review). *Test:* every task node has the glyph + `data-status`; no badge says "merge". *(Accessibility.)*
- **UX-DR4 — Static, no motion; emphasis on blocked.** No `transition`/`animation`/`@keyframes`/`scroll-behavior:smooth` in CSS, no `requestAnimationFrame`/timer-driven style mutation in JS. **Blocked** is the only lifted card; running flat; done dimmed. *Test:* grep CSS/JS for the forbidden tokens → absent. Satisfies `prefers-reduced-motion`.
- **UX-DR5 — Stable render via diff-and-patch.** Poller keys by `task_id`, content-hash per task; identical snapshot → zero DOM writes; change → patch only the changed node; status change → reparent (not recreate); disclosure open-state + scroll survive polls. *Test:* `diff(x, x) === []` (`node --test`); identical consecutive `/state` → `MutationObserver` records 0 mutations. *(FR-9.)*
- **UX-DR6 — Subordinate freshness + stale threshold.** `generated_at` rendered small/cornered; stale treatment (grey→amber) triggers when age **> 2 × poll-interval**. *Test:* render with a stamp older than the threshold → the stale class is present; under → absent. *(FR-9.)*
- **UX-DR7 — Demoted Done + orphan disclosures.** Done (`✓ N done`) and orphan annotations each in a collapsed-by-default `<details>` below the board, self-explaining, never auto-expanded. *Test:* both `<details>` lack `open` on first render. *(FR-12 view.)*
- **UX-DR8 — git-unavailable & per-repo degradation.** On git failure the board shows last-known data with an explicit "stale — git unavailable" label (never blank); a single slow/timed-out repo renders only its own worktree lines as "unavailable". *Test:* given a degraded `/state`, assert the label text + that other repos render normally. *(Architecture degrade rule.)*
- **UX-DR9 — Empty states.** Empty column → header + "0"; **empty Blocked → "Nothing needs you"**; fully empty board → "No active tasks — create one with `create_task`"; zero done → no done-disclosure. *Test:* assert each copy string for the corresponding empty payload.
- **UX-DR10 — Self-contained, no external assets.** All CSS/JS inline, system font stack only; no network egress. *Test:* grep rendered HTML for `http://`/`https://`/`//`/external `src`/`href`/`@import` → none. *(NFR-Security/Locality, NFR-Simplicity.)*
- **UX-DR11 — WCAG AA contrast (enumerated pairs).** Each status text + bar/glyph against `{bg}` and `{surface}` meets AA (text ≥4.5:1, non-text ≥3:1). *Test:* a pure contrast-ratio function over the enumerated token pairs asserts the thresholds.
- **UX-DR12 — Overflow contract.** Active columns bounded by agent count fit without scroll; an over-full column scrolls within itself; the board never scrolls horizontally (3→1 wrap at narrow width). *Test:* board container has `overflow-x` none/hidden; column has `overflow-y:auto`.
- **UX-DR13 — Done is a folded count, not a column.** Done tasks never occupy a board column; they live only in the done-disclosure; the done count remains in the summary bar when folded. *Test:* no Done column in the grid; done tasks appear only inside the `<details>`.
