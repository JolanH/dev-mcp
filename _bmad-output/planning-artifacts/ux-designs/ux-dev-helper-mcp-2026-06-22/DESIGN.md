---
title: dev-helper-mcp dashboard — Visual Identity
status: final
created: 2026-06-22
updated: 2026-06-22
sources:
  - ../../prds/prd-dev-helper-mcp-2026-06-19/prd.md
  - ../../architecture.md
colors:
  bg: "#0e1117"
  surface: "#161b22"
  border: "#21262d"
  bar-done: "#373e47"
  text: "#e6edf3"
  text-muted: "#8b949e"
  text-dim: "#586069"
  worktree-repo: "#c9d1d9"
  running: "#39d0a8"
  running-border: "#1f3f37"
  blocked: "#e3a34a"
  blocked-bg: "#2a2113"
  blocked-border: "#3d3320"
  review: "#6cb6ff"
  review-bg: "#16263d"
  review-border: "#1f3a5c"
  done: "#7d8590"
typography:
  sans: "ui-sans-serif, system-ui, sans-serif"
  mono: "ui-monospace, 'SF Mono', Menlo, monospace"
  size-task-title: "13px"
  size-column-header: "11.5px"
  size-worktree: "11.5px"
  size-badge: "10.5px"
  size-freshness: "11.5px"
  weight-title: 650
  weight-header: 700
rounded:
  card: "8px"
  pill: "999px"
  badge: "5px"
spacing:
  page-padding: "16px 18px 26px"
  card-padding: "9px 11px 9px 14px"
  card-gap: "8px"
  column-gap: "10px"
  status-bar-width: "3px"
components:
  - board
  - summary-bar
  - status-pill
  - task-card
  - status-bar
  - reason-badge
  - worktree-line
  - done-disclosure
  - orphan-disclosure
  - freshness-stamp
---

# dev-helper-mcp dashboard — Visual Identity

> Distilled from `.decision-log.md`. This DESIGN.md owns *how it looks*; `EXPERIENCE.md` owns *how it works* and references these tokens by name. Both spines win over any mock. Reference mock: `mockups/key-screen-board.html`.

## Brand & Style

A calm, dense **developer-console** surface — a monitoring tab left open beside the editor all day. "Modern console, compact": the refined, rounded-card feel of a product UI at the information density of an ops board. The aesthetic is quiet by default so that **color and position do the talking** — the eye should land on what needs the human (blocked, review) and what is alive (running) without reading. **No motion**: nothing animates, ever (a perpetually-open tab must not pulse or distract; also satisfies `prefers-reduced-motion` by construction).

## Colors

Dark surface, four saturated status hues that stay distinct against `{colors.bg}` and from each other:

- **Surfaces** — page `{colors.bg}` (#0e1117), card `{colors.surface}` (#161b22), hairline `{colors.border}` (#21262d).
- **Text** — primary `{colors.text}`, secondary `{colors.text-muted}`, tertiary/freshness `{colors.text-dim}`; worktree repo names `{colors.worktree-repo}`.
- **Status (the load-bearing palette):**
  - Running — `{colors.running}` teal, tint border `{colors.running-border}`.
  - Blocked — `{colors.blocked}` amber, badge bg `{colors.blocked-bg}`, border `{colors.blocked-border}`.
  - Review — `{colors.review}` blue, badge bg `{colors.review-bg}`, border `{colors.review-border}`.
  - Done — `{colors.done}` grey; the card's left bar uses the dimmer `{colors.bar-done}`.

Status color is applied to: the card's **left bar**, the per-card **glyph** (●/▲/◆/✓), the **column header**, and the **summary pill**. Done cards (inside the done-disclosure) additionally drop to `opacity:~.55`.

## Typography

System `{typography.sans}` for all chrome and titles; `{typography.mono}` for repo/branch/path lines (they are code-like identifiers and read better monospaced). Compact scale: task titles `{typography.size-task-title}`/650, uppercase column headers `{typography.size-column-header}`/700 with `.05em` tracking, worktree lines `{typography.size-worktree}`, badges `{typography.size-badge}`, freshness `{typography.size-freshness}`.

## Layout & Spacing

Single board, `max-width:1000px`, centered, page padding `{spacing.page-padding}`. A **summary bar** of status-count pills sits above a **3-column grid** (`repeat(3,1fr)`, gap `{spacing.column-gap}` — Running | Blocked | Review), `align-items:start` so columns are independent. Below the board sit two collapsed disclosures in order: **done-disclosure** then **orphan-disclosure**. Cards stack with `{spacing.card-gap}` gaps, padding `{spacing.card-padding}` (extra left pad clears the status bar). Compact throughout. **Overflow contract:** active columns are bounded by agent count (2–4 typical) and fit without scroll; a column that overflows scrolls **within the column**; the board never scrolls horizontally (narrow widths wrap; mobile out of scope).

## Elevation & Depth

Near-flat, with depth reserved for **urgency, not activity**. Cards carry a 1px `{colors.border}` and a whisper shadow (`0 1px 2px rgba(0,0,0,.3)`). **Blocked** cards are the only lifted element — the alarm state: a tinted ring + amber bloom (`0 0 0 1px {colors.blocked-border}, 0 2px 12px rgba(227,163,74,.14)`). An idle agent awaiting input is the most urgent thing on the board, so it gets the weight. **Running is the calm flat baseline** (it needs nothing from the operator); **Review** is flat (blue bar). **Done** is folded away entirely (recedes via opacity inside its disclosure). Depth, not motion, marks "needs you." Urgency gradient: blocked → review → running → done.

## Shapes

Rounded cards `{rounded.card}`, fully-round summary pills `{rounded.pill}`, small-radius badges `{rounded.badge}`. The **status bar** is a `{spacing.status-bar-width}` square-cut stripe on the card's left edge, clipped to the card radius via `overflow:hidden`. The per-card status **glyph** is a small mono shape (●/▲/◆/✓) in the title row — a shape-distinct, non-color channel.

## Components

- **board** — a **3-column** grid of the active states: Running · Blocked · Review. `done` is NOT a column (see `done-disclosure`).
- **summary-bar** — row of `status-pill`s (count per status, including done) + a right-aligned `freshness-stamp`.
- **status-pill** — rounded pill, status glyph + count + label; status-colored text/border. The blocked pill is bolder (weight 700).
- **task-card** — title row (`status-bar` + per-card **glyph** + task name + optional `reason-badge`) over one or more `worktree-line`s. Variant per status; **blocked** is lifted (see Elevation), running/review flat.
- **status-bar** — left-edge color stripe encoding status (redundant with column + glyph).
- **reason-badge** — small right-aligned chip with the human-readable reason ("needs input" amber on `{colors.blocked-bg}`; **"awaiting review"** blue on `{colors.review-bg}`). No "merge" wording (merge-back is out of scope).
- **worktree-line** — monospace `repo · branch` (path on hover/title); one per repo the task spans.
- **done-disclosure** — a `<details>` below the board, collapsed by default, summary `✓ N done`; expands to dimmed done cards. Keeps the no-action history out of the glance while remaining one click away.
- **orphan-disclosure** — a `<details>` below the done-disclosure; collapsed by default; self-explaining summary.
- **freshness-stamp** — subordinate `generated_at` text (`{colors.text-dim}`); greys further / shifts amber when older than **2× the poll interval** (stale).

## Do's and Don'ts

- **Do** let column position + per-card glyph carry status; color is reinforcement, not the sole channel.
- **Do** reserve the only lift/weight for **blocked** (the alarm); keep running calm and flat; fold done away.
- **Do** put the glyph (●/▲/◆/✓) on **every card**, not just the column header — it must travel with the card for color-blind safety.
- **Don't** animate anything — no pulses, spinners, or transitions on poll.
- **Don't** make running the loudest element — it needs nothing from the operator.
- **Don't** give `done` a board column or promote orphans into the glance path; both stay collapsed below the board.
- **Don't** say "merge" anywhere — review is "awaiting your review," not "ready to merge."
- **Don't** introduce a fifth status color or a second accent; the four status hues are the whole palette.
