# Story 2.4b: Live poller with diff-and-patch stable render

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want the open board to track `/state` live without flicker or losing my place,
so that I can leave the tab open and trust it to silently stay correct.

## Acceptance Criteria

1. **Live reflect ≤3s, no manual reload (FR-9).**
   **Given** the page open,
   **When** an agent updates a task's status,
   **Then** the page reflects it within ≤ 3s (≤ 15 repos) with no manual reload (vanilla-JS poll of `/state` ~1–2s).

2. **Diff is empty on identical snapshots — zero DOM writes (UX-DR5).**
   **Given** the poller's `diff(prev, next)` function keyed by `task_id` with a per-task content hash,
   **When** two identical `/state` snapshots are diffed,
   **Then** it returns an empty patch set (`diff(x, x) === []`) — asserted by a `node --test` unit test — **and** a `MutationObserver` over the board container records **zero** mutations across the identical poll.

3. **Status change reparents, preserves disclosure/scroll (UX-DR5).**
   **Given** a task whose status changes between polls,
   **When** the patch applies,
   **Then** the existing DOM node is **reparented** to the new column (not destroyed/recreated), only changed fields are patched, and the open/closed state of the Done/orphan disclosures and any scroll position are preserved.

4. **Served UI is read-only (FR-10).**
   **Given** the served UI,
   **When** the user interacts with it,
   **Then** it offers no control to create/modify/remove worktrees or tasks or to launch agents (read-only).

## Tasks / Subtasks

- [ ] **Task 1 — `dashboard/static/app.js`: the poller (AC: 1)**
  - [ ] Vanilla JS (no framework, no build, no external `src`): `fetch('/state')` every ~1–2s (interval from a small inline constant matching the architecture poll interval); hold the last snapshot
  - [ ] On each poll: compute `diff(prev, next)` and apply the patch set to the existing DOM (never `innerHTML = ...` wholesale)
- [ ] **Task 2 — the `diff(prev, next)` function — the mechanical contract (AC: 2, 3)**
  - [ ] Key by `task_id`; compute a per-task **content hash** over `(status, slug/task_id, repos, reason, freshness-relevant fields)`
  - [ ] key absent in `next` → remove node; key new → create + insert in **sorted position** (task_id ASC, matching 2.1); key in both & hash equal → **no patch**; hash changed → patch only the changed fields
  - [ ] A **status change reparents** the existing node to the new column via `insertBefore` (never destroy-and-recreate); keep node identity stable
  - [ ] `diff(x, x)` returns `[]` (empty patch set) — pure function, unit-testable in node with no DOM
- [ ] **Task 3 — preserve operator place (AC: 3)**
  - [ ] Patching must not touch the Done/orphan `<details>` `open` state or reset scroll position; only patch changed task nodes/fields
  - [ ] Node shape produced by the JS must be **identical** to 2.4a's server-rendered markup (same `data-status`, glyph ●/▲/◆/✓, classes) so first-paint → first-poll causes zero churn
- [ ] **Task 4 — read-only (AC: 4)**
  - [ ] The JS adds no mutating control, form, or fetch with a mutating method; only `GET /state`. No button creates/edits/removes a task/worktree or launches an agent
- [ ] **Task 5 — no motion (UX-DR4 carryover)**
  - [ ] No `requestAnimationFrame`/timer-driven style mutation; status changes appear as a state swap, not an animation (the poll timer is allowed; style-animating timers are not)
- [ ] **Task 6 — tests (browser-free where possible, under AR-12 gate)**
  - [ ] `node --test` unit test for `diff()`: `diff(x, x) === []`; add/remove/reparent/field-change cases produce the expected minimal patch set (this is the **one small JS test added to the AR-12 gate**)
  - [ ] Identical-poll zero-mutation: a `MutationObserver` over the board records **zero** mutations across two identical `/state` payloads (jsdom in the node test, or document the harness); status-change applies a reparent, not a recreate
  - [ ] Static lint (grep, from 2.4a): JS has no external `src`, no `requestAnimationFrame`/style-animating timer; no mutating fetch method

## Dev Notes

### Scope boundaries — read first
Adds the **live diff-and-patch poller** on top of 2.4a's static board. **OUT of scope:** the freshness-stale visuals, git-unavailable/per-repo-degrade rendering, and empty-state copy (Story 2.4c — though the poller must not break them when they arrive; keep the diff field-set extensible to include freshness/degrade fields). The board structure, CSS, and node shape come from 2.4a — do not re-create them.

### The diff-and-patch mechanical contract (pinned — EXPERIENCE.md § Interaction Primitives)
The poller does NOT replace `innerHTML` wholesale. It holds the last snapshot and computes a per-`task_id` delta:
- key absent in new → remove node; key new → create + insert in sorted position;
- key in both → compare a per-task content hash (status, slug, repos, reason, freshness); **equal → no DOM write at all**; changed → patch only the changed fields on the existing node;
- a status change **reparents** the existing node (`insertBefore`), never destroy-and-recreate.
This preserves the Done/orphan disclosure open-state and scroll across polls. Testable: identical consecutive `/state` → `diff()` returns an empty patch set (`diff(x,x) === []`), and a `MutationObserver` records **zero** mutations. [Source: EXPERIENCE.md#Interaction Primitives; epics.md#UX-DR5]

### Why this matters (north star)
A perpetually-open tab must stay correct without flicker, motion, or losing the operator's place — the SM-2 <10s glance depends on stable render. Wholesale re-render would reset disclosures/scroll and (with motion) distract. [Source: EXPERIENCE.md#Key Flows; DESIGN.md#Brand & Style — no motion]

### Builds on Stories 2.3 + 2.4a (previous-story intelligence)
- Polls the `GET /state` endpoint from **2.3** (read-only, cache-backed, snake_case JSON). Read the same snake_case field names.
- Patches the DOM produced by **2.4a**'s server render; node identity is keyed by `task_id` and carries `data-status` + glyph — reuse exactly. The JS render path and server render path must produce identical markup (single source of truth for the card template).
- Read-only (FR-10) — same guarantee as 2.3/2.4a; the JS introduces no mutation.

### Binding invariants / requirements
- **FR-9** ≤3s freshness (bounded ≤15 repos) via ~1–2s polling. **FR-10** read-only. **UX-DR4** no motion (the poll timer is fine; style-animating timers/RAF are not). **UX-DR5** diff-and-patch stable render. [Source: epics.md#FR-9, #FR-10, #UX-DR4, #UX-DR5]

### Source tree components to touch
`dashboard/static/app.js` (the poller + `diff()`); a `node --test` test file for `diff()` (added to the AR-12 gate — the gate now runs `ruff` + `pytest` + this one `node --test`); reuse 2.4a's card template. [Source: architecture.md#Complete Project Directory Structure — dashboard/static/app.js; epics.md#AR-12 — the one small JS test]

### Project Structure Notes
- `app.js` ships inside the package, inline-served, no external assets (UX-DR10 carryover). [Source: EXPERIENCE.md#Foundation]
- The AR-12 gate gains its single JS test here — confirm the pre-commit hook runs `node --test` (Story 1.1 established the hook; this extends what it runs). [Source: epics.md#AR-12]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 2.4b: Live poller with diff-and-patch stable render] — acceptance criteria
- [Source: epics.md#FR-9, #FR-10, #UX-DR5, #AR-12]
- [Source: EXPERIENCE.md#Interaction Primitives] — the diff-and-patch contract
- [Source: architecture.md#Frontend Architecture] — vanilla-JS poller, stable render across polls

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
