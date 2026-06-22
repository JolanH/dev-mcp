# Story 2.4c: Freshness, degraded, and empty states

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want the board to stay honest when data is stale, git is down, or there's nothing to show,
so that I'm never misled by a blank or silently-behind dashboard.

## Acceptance Criteria

1. **Subordinate freshness + stale threshold (UX-DR6).**
   **Given** the freshness stamp,
   **When** the snapshot age exceeds **2 × the poll interval**,
   **Then** `generated_at` (rendered small/cornered) shows the stale treatment (grey→amber); under the threshold it does not.

2. **Demoted Done + orphan disclosures (UX-DR7).**
   **Given** done tasks and orphaned annotations,
   **When** the page renders,
   **Then** the `✓ N done` and orphan sections are each a collapsed-by-default `<details>` below the board, self-explaining, never auto-expanded.

3. **git-unavailable + per-repo degrade (UX-DR8).**
   **Given** a git-unavailable refresh or a single slow/timed-out repo,
   **When** the page renders,
   **Then** it shows labeled last-known data with an explicit "stale — git unavailable" marker (never a blank board), and a single slow repo degrades only its own worktree lines ("unavailable") while other repos render normally.

4. **Empty states (UX-DR9).**
   **Given** empty states,
   **When** the page renders,
   **Then** an empty column shows header + "0"; the **empty Blocked column reads "Nothing needs you"**; a fully empty board shows "No active tasks — create one with `create_task`"; a zero-done state omits the done-disclosure.

## Tasks / Subtasks

- [ ] **Task 1 — freshness stamp + stale treatment (AC: 1)**
  - [ ] Render `generated_at` subordinate (small, cornered, `text-dim` `#586069`) as relative age ("updated 1s ago")
  - [ ] When age **> 2 × poll-interval** (~3–4s): apply the stale class (grey→amber shift, e.g. "updated 7s ago"); under threshold: no stale class. The poller (2.4b) recomputes age each tick
- [ ] **Task 2 — Done + orphan disclosures, collapsed by default (AC: 2)**
  - [ ] `done-disclosure` `<details>` (summary `✓ N done`) and `orphan-disclosure` `<details>` below the board; **neither has `open` on first render**; never auto-expanded even when non-empty
  - [ ] Orphan summary is self-explaining: "branch gone from git, note preserved here"; expanding lists `branch — branch gone from git, note preserved here`
  - [ ] The Done count stays in the summary bar even when folded; the poller preserves the `open` state across polls (2.4b contract)
- [ ] **Task 3 — git-unavailable + per-repo degrade rendering (AC: 3)**
  - [ ] When the snapshot is marked stale/git-unavailable (from 2.2's cache stale flag): show last-known data with an explicit **"stale — git unavailable"** label; **never a blank board**
  - [ ] A single repo's worktree lines marked unavailable (per-repo degrade from 2.2) render as "unavailable / last-known" for **that repo only**; other repos render normally
- [ ] **Task 4 — empty states (AC: 4)**
  - [ ] Empty active column → header + "0" + quiet empty space (no placeholder card)
  - [ ] **Empty Blocked column → "Nothing needs you"** (exact copy)
  - [ ] Fully empty board (no tasks anywhere) → "No active tasks — create one with `create_task`" (exact copy)
  - [ ] Zero-done → omit the done-disclosure entirely
- [ ] **Task 5 — tests (browser-free, under AR-12 gate)**
  - [ ] UX-DR6: render with a stamp older than the threshold → stale class present; under → absent
  - [ ] UX-DR7: both `<details>` lack `open` on first render (even when non-empty)
  - [ ] UX-DR8: given a degraded `/state` (git-unavailable / one slow repo), assert the "stale — git unavailable" label text and that other repos render normally
  - [ ] UX-DR9: assert each exact copy string for the corresponding empty payload ("Nothing needs you", the empty-board line, zero-done omits disclosure, empty column header + "0")

## Dev Notes

### Scope boundaries — read first
Layers the **honesty/edge states** onto 2.4a's structure + 2.4b's poller. This is the LAST Epic 2 story — after it, the dashboard is complete. **OUT of scope:** nothing new beyond the four UX-DRs; do not alter the happy-path structure (2.4a) or the diff contract (2.4b) except to add the freshness/degrade/empty fields to the render + diff hash.

### Exact copy strings (EXPERIENCE.md is binding — assert these verbatim)
- Empty Blocked column: **"Nothing needs you"**
- Fully empty board: **"No active tasks — create one with `create_task`"** (informative, plain)
- Orphan disclosure: **"branch gone from git, note preserved here"**
- Freshness: relative age, e.g. "updated 1s ago" → stale "updated 7s ago"
- git-unavailable: explicit **"stale — git unavailable"** label
No "merge" anywhere; lowercase reason fragments; numbers over prose. [Source: EXPERIENCE.md#Voice and Tone, #State Patterns, #Component Patterns, #Design Requirements UX-DR9]

### Stale / degrade come from the data layer (architecture.md + Stories 2.1/2.2)
- The **stale flag** and `generated_at` come from the cache (2.2): on a failed/partial refresh the cache keeps last-known and marks it stale — the board reads that flag; it does not decide staleness from git itself (it has no git on the poll path). [Source: architecture.md#Derived State & Refresh Model; epics.md#Story 2.2]
- **Per-repo degrade** is represented in the snapshot (2.2 renders one slow repo's worktrees as unavailable/last-known); 2.4c just displays it. A blank board is forbidden — it would read as "no work," a lie. [Source: EXPERIENCE.md#State Patterns — git-unavailable / per-repo degradation]
- **Orphans** come from 2.1's `warnings` + `WorktreeView.orphaned`; never auto-expanded, never auto-deleted. [Source: architecture.md#Orphaned-link rule; EXPERIENCE.md#Component Patterns]

### Freshness threshold (EXPERIENCE.md § State Patterns)
Stale treatment triggers when the snapshot is older than **2 × the poll interval** (~3–4s for a ~1–2s poll). Subordinate placement (small/cornered, `text-dim`); greys further / shifts amber when stale — signalling "may be behind" without alarming. [Source: EXPERIENCE.md#State Patterns — Freshness/staleness; DESIGN.md#components freshness-stamp]

### Builds on Stories 2.1, 2.2, 2.4a, 2.4b (previous-story intelligence)
- Reads the `CacheSnapshot` stale flag + `generated_at` (2.2), `warnings`/`orphaned` (2.1), and renders into 2.4a's structure via 2.4b's poller.
- Extend 2.4b's per-task content hash to include freshness/degrade-relevant fields so the diff repaints them when they change (without breaking `diff(x,x)===[]`).
- Reuse the exact DESIGN tokens from 2.4a (text-dim `#586069`, blocked amber `#e3a34a`, etc.). Disclosure `open`-state preservation is the 2.4b contract — confirm it holds for these sections.

### Source tree components to touch
`dashboard/static/index.html`/render helper (freshness stamp, empty-state copy, degrade labels, disclosures), `dashboard/static/style.css` (stale class, dimmed disclosure), `dashboard/static/app.js` (recompute age each tick; include freshness/degrade in the diff hash); tests extending the 2.4a HTML-output suite. [Source: architecture.md#Complete Project Directory Structure — dashboard/static]

### Project Structure Notes
- Done + orphan stay **below the board, collapsed** — never promoted into the glance path (UX-DR7/UX-DR13). [Source: EXPERIENCE.md#Information Architecture]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 2.4c: Freshness, degraded, and empty states] — acceptance criteria + UX-DR mapping (UX-DR6/7/8/9; UX-DR8 pairs with Story 2.2)
- [Source: EXPERIENCE.md#State Patterns, #Component Patterns, #Voice and Tone, #Design Requirements] — stale threshold, degrade, empty copy, orphan summary
- [Source: DESIGN.md#components] — freshness-stamp, done-disclosure, orphan-disclosure tokens
- [Source: architecture.md#Derived State & Refresh Model, #Orphaned-link rule] — stale flag origin, orphan rule

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
