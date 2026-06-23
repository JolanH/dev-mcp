---
stepsCompleted: ['step-01-document-discovery', 'step-02-prd-analysis', 'step-03-epic-coverage-validation', 'step-04-ux-alignment', 'step-05-epic-quality-review', 'step-06-final-assessment']
status: 'complete'
documentsIncluded:
  - prds/prd-dev-helper-mcp-2026-06-19/prd.md
  - prds/prd-dev-helper-mcp-2026-06-19/addendum.md
  - architecture.md
  - epics.md
  - ux-designs/ux-dev-helper-mcp-2026-06-22/DESIGN.md
  - ux-designs/ux-dev-helper-mcp-2026-06-22/EXPERIENCE.md
---

# Implementation Readiness Assessment Report

**Date:** 2026-06-22
**Project:** dev-helper-mcp

## Document Inventory

| Type | Format | Path | Status |
|------|--------|------|--------|
| PRD | Sharded | `prds/prd-dev-helper-mcp-2026-06-19/prd.md` (+ `addendum.md`) | ✅ Found |
| Architecture | Whole | `architecture.md` | ✅ Found |
| Epics & Stories | Whole | `epics.md` | ✅ Found |
| UX Design | Sharded | `ux-designs/ux-dev-helper-mcp-2026-06-22/DESIGN.md` + `EXPERIENCE.md` | ✅ Found |

**Issues:** No duplicates. No missing required documents.

## PRD Analysis

### Functional Requirements

- **FR-1: Create a task with one worktree+branch per repository** — In a single call (`create_task`), agent supplies description, set of repo paths (1+), task name; server creates one worktree on branch `agent/<task>` per repo from each repo's base ref (default HEAD), in sibling dir `<repo-parent>/<repo>.worktrees/<task>/`. Validates each path is a git repo; all-or-nothing across the repo set; structured errors on branch/dir collision, missing base ref, stale dirs; error-safe rollback with `RollbackIncomplete` on compensation failure.
- **FR-2: List worktrees** — List all worktrees tracked across all repos any active task touches; each with repo, path, branch, linked task/status; filterable by repo and/or task; derived from git, not just cache.
- **FR-3: Remove worktree** — Remove one tracked worktree (by task+repo or path), optionally delete its branch. Refuses on uncommitted changes without `force`; branch deletion guarded separately with distinct `force_unmerged_branch` flag. Per-worktree; other repos unaffected. Merge-back out of scope.
- **FR-4: Register task** — Agent creates a task (via `create_task`) with description and repo set, default status `running`. Persisted with stable `task_id`, timestamps, per-repo worktree links. At most one active (non-`done`) task per `<task>` slug, enforced atomically. Slug reusable after `done`.
- **FR-5: Update task status and description** — Agent updates an existing task's status and/or description; bumps updated timestamp; not-found error for unknown task.
- **FR-6: Canonical task status set** — Fixed four states: `running`, `blocked`, `review`, `done`. `review` = active (awaiting operator review); only `done` is closed/terminal. Out-of-set values rejected; `done` dimmed on dashboard.
- **FR-7: List tasks** — List tasks filterable by status and/or repo, each including per-repo worktree links and all model fields.
- **FR-8: Live monitoring view** — Read-only board: three active status columns (Running | Blocked | Review), `done` foldable count below. Each task card shows description, status, per-repo worktrees (repo, branch, path). Blocked is emphasized alarm state; states distinct by position, color, left bar, glyph. Per UX spec.
- **FR-9: Automatic refresh** — Dashboard updates without manual reload; v1 = short-interval polling of state endpoint (SSE = vNext). State changes visible ≤ 3s (for ≤ 15 tracked repos).
- **FR-10: Read-only guarantee** — Dashboard exposes no mutating action (no create/modify/remove worktrees or tasks, no agent launch).
- **FR-11: Discoverable, documented tool surface** — Server advertises tools over MCP with clear names/descriptions and input/output schemas; small tool count (well under ~40 client cap).
- **FR-12: State persistence and git-derived view** — Task records + per-repo worktree links persist across restarts in machine-global store; live view derived per-repo via `git worktree list --porcelain` as source of truth. Detects out-of-band creation/removal; derived view cached (computed on tool calls + periodic background tick, not per poll).
- **FR-13: Global server lifecycle** — Developer starts one long-lived global server (one per machine), serving both MCP endpoint and dashboard, bound to `127.0.0.1`. Outlives agent sessions; prints dashboard URL on startup; single instance enforced (refuse/attach, not opaque port error); not reachable remotely.

**Total FRs: 13**

### Non-Functional Requirements

- **NFR-Performance** — Task/state tool calls sub-second; worktree creation bounded by `git worktree add` (typical repo sub-second, may be several seconds on large/cold repo); never approach MCP transport timeout (~5 min). Dashboard state visible ≤ 3s for ≤ 15 tracked repos (bounded by repo count; slow repo degrades only that repo).
- **NFR-MCP-Compatibility** — Tool calls protocol-synchronous & short-lived but must NOT block the event loop (git shell-outs off-loop via async subprocess/worker thread); tool count small (well under ~40).
- **NFR-Reliability** — State survives restart; reconcile tracked state vs actual git worktrees so dashboard never lies; structured/actionable tool errors; failed git ops leave repo unchanged.
- **NFR-Security/Locality** — Localhost-only bind; no remote exposure; no secrets; single-user trust; both MCP endpoint and dashboard routes must validate `Origin` header and reject non-localhost origins (DNS-rebinding protection, mandated by MCP HTTP transport spec).
- **NFR-Portability** — Linux primary; avoid hard platform-specific assumptions so macOS works.
- **NFR-Simplicity/Footprint** — Single easy local install, minimal dependencies; cheap to run alongside several agents.
- **NFR-Observability** — Local logs sufficient to diagnose a failed tool call or reconciliation mismatch.

**Total NFRs: 7**

### Additional Requirements & Constraints

- **Stack (addendum §1, D5):** Python + official MCP SDK; git access via shell-out to `git` CLI; single long-lived machine-global asyncio process hosting MCP + ASGI dashboard; `git -C <repo>` per task.
- **Transport (addendum §2, D10):** Streamable HTTP transport (not stdio); registered in Claude Code as HTTP/URL MCP server once globally. No `--repo` flag — repos supplied implicitly per task as absolute paths.
- **Single-instance/port (addendum §2):** Default port 8765 with fallback scan; global lockfile (XDG state dir) + port probe; refuse-or-attach.
- **Persistence (addendum §4, A2):** SQLite DB in machine-global XDG state dir (`~/.local/state/dev-helper-mcp/state.db`), WAL mode + busy_timeout; two tables (`task`, `task_worktree`); slug = primary key for atomic one-active-per-slug.
- **Concurrency (addendum §4):** Per-repo async mutation mutex keyed by `repo_path`; mutation pool (sem=4); read-pool semaphore for git fan-out.
- **`create_task` atomicity (addendum §4):** Preflight all repos before mutating; commit rows last in one SQLite transaction; compensating teardown on failure; `RollbackIncomplete` named-orphan error; full crash-safety a documented v1 non-goal.
- **Tool schemas (addendum §3):** 5 tools — `create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks`.
- **Safety/Privacy/Cost guardrails (PRD):** explicit intent on destructive ops; no force-push/mainline rewrite; no telemetry; zero cost (local).
- **MCP tool contract stability:** tool names/shapes are a public contract; breaking changes = versioning event.

### Non-Goals (explicit, for traceability)

Agent launch/supervision (D3); merge-back/rebase/PR automation (D7); dashboard write actions (D4); auth/remote/multi-user (A3); replacement for native `--worktree` (R2); same-file collision pre-warning; worktree bootstrap automation (`.env`/deps/ports); per-repo base-ref overrides on create; incremental `add_worktree` to existing task — all deferred post-v1.

### PRD Completeness Assessment

The PRD is unusually rigorous: every FR has explicit testable consequences, NFRs are concrete and bounded, assumptions are indexed (A1–A5) with most resolved, and a 2026-06-22 multi-repo amendment is fully threaded through. The companion addendum supplies the technical contract (transport, schemas, persistence). Initial read shows strong internal consistency; epic-coverage validation follows.

## Epic Coverage Validation

### Coverage Matrix

| FR | PRD Requirement (short) | Epic / Story Coverage | Status |
|----|--------------------------|------------------------|--------|
| FR-1 | Create task w/ worktree+branch per repo (all-or-nothing) | Epic 1 — Story 1.3 (happy path), 1.4 (rollback) | ✓ Covered |
| FR-2 | List worktrees across all tracked repos | Epic 1 — Story 1.5 | ✓ Covered |
| FR-3 | Remove worktree (two-guard force semantics) | Epic 1 — Story 1.5 | ✓ Covered |
| FR-4 | Register task; one-active-per-slug; persistence | Epic 1 — Store in 1.2, conflict rule in 1.3 | ✓ Covered |
| FR-5 | Update task status/description | Epic 1 — Story 1.6 | ✓ Covered |
| FR-6 | Canonical four-state status set | Epic 1 — Story 1.6 (4×4 transition matrix), 1.3 (active=non-done) | ✓ Covered |
| FR-7 | List tasks (filter status/repo) | Epic 1 — Story 1.6 | ✓ Covered |
| FR-8 | Live monitoring board (3 cols + folded Done) | Epic 2 — Story 2.4a | ✓ Covered |
| FR-9 | Automatic refresh ≤3s (polling) | Epic 2 — Story 2.4b (poller), 2.2 (refresher) | ✓ Covered |
| FR-10 | Read-only guarantee | Epic 2 — Story 2.3 (endpoint), 2.4b (UI) | ✓ Covered |
| FR-11 | Discoverable 5-tool MCP surface | Epic 1 — Story 1.6 (enumerate 5), 1.1 (skeleton) | ✓ Covered |
| FR-12 | State persistence + git-derived view | Split: Store in Epic 1 (1.2); per-repo derive-on-read view in Epic 2 (2.1/2.2/2.3) | ✓ Covered |
| FR-13 | Global server lifecycle / single-instance | Split: minimal bootstrap Epic 1 (1.1); full lockfile/port/install Epic 3 (3.1/3.2/3.3) | ✓ Covered |

### Missing Requirements

**None.** All 13 PRD Functional Requirements trace to at least one epic and story. No orphan FRs in the epics (no epic claims an FR absent from the PRD).

**Split-FR note (not a gap, flagged for awareness):** Two FRs are deliberately split across epics with a documented primary home — FR-12 (machine-global `Store` lands in Epic 1 Story 1.2; the per-repo derive-on-read *view* lands in Epic 2) and FR-13 (a minimal runnable global server is bootstrapped in Epic 1 Story 1.1; full single-instance/lockfile/port/install hardening lands in Epic 3). This is sound sequencing (foundation-first), but means neither FR-12 nor FR-13 is fully satisfied until its later epic completes — relevant if epics are delivered incrementally.

### Non-Functional & Supplementary Coverage (informational)

- **NFRs:** PRD's 7 NFRs map 1:1 to the epics' NFR-1…NFR-7, with concrete ACs woven into stories (e.g. NFR-4 Origin validation → Story 1.1 + 2.3; NFR-1 ≤3s SLO fan-out chaos test → Story 2.2).
- **Architecture requirements (AR-1…AR-14):** all carried into Epic 1's foundation stories (1.1/1.2) or their owning stories (AR-13 rollback → 1.4; AR-14 per-repo mutex → 1.2; AR-12 quality gate → 3.3).
- **UX Design Requirements (UX-DR1…UX-DR13):** all 13 mapped to Epic 2 Stories 2.4a/2.4b/2.4c with machine-checkable predicates.

### Coverage Statistics

- **Total PRD FRs:** 13
- **FRs covered in epics:** 13
- **Coverage percentage:** 100%
- **Orphan FRs (in epics, not in PRD):** 0

## UX Alignment Assessment

### UX Document Status

**Found** — a complete, two-spine UX specification at `ux-designs/ux-dev-helper-mcp-2026-06-22/`:
- `DESIGN.md` — visual identity (dark "modern console" palette, 4 status colors, type/spacing tokens, 10 named components).
- `EXPERIENCE.md` — IA, voice/tone, behavioral component patterns, state/empty/degraded patterns, accessibility floor, the UJ-1/SM-2 glance flow, and the authoritative **UX-DR1–13** with machine-checkable predicates.
- `mockups/key-screen-board.html` — reference mock (spines win over the mock by explicit statement).

### UX ↔ PRD Alignment

| UX element | PRD anchor | Status |
|------------|-----------|--------|
| 4-state set (running/blocked/review/done) + semantics | FR-6 (verbatim match incl. "review ≠ merge") | ✓ Aligned |
| Read-only board, no mutating control | FR-10 | ✓ Aligned |
| 3 active columns + folded Done, blocked-emphasis | FR-8 (`[AMENDED 2026-06-22b]`) | ✓ Aligned |
| Polling, ≤3s freshness, bounded to ≤15 repos | FR-9 + NFR-Performance | ✓ Aligned |
| Per-repo degradation / git-unavailable last-known | FR-12 + NFR-Reliability | ✓ Aligned |
| Self-contained, no external assets | NFR-Security/Locality, NFR-Simplicity | ✓ Aligned |
| SM-2 "<10s glance" north star | Success Metric SM-2 | ✓ Aligned |
| "Never say merge" voice rule | Non-goal: no merge-back (D7) | ✓ Aligned |

No UX requirement was found that lacks a PRD anchor. The UX scope is strictly the dashboard surface (FR-8/9/10 + the FR-12 view), which the PRD already owns.

### UX ↔ Architecture Alignment

Architecture's **Frontend Architecture (dashboard)** section (line 479) and pinned cached-view shape (line 362) support every UX behavioral contract:
- `/state` JSON served **from the in-memory cache only — never shells out to git on a poll** (arch invariant #5) — matches UX polling/freshness model.
- `dashboard/routes.py` (`GET /`, `GET /state`) + `app.js` poller (`poll /state ~1–2s; stable render`) in the directory structure (lines 718–721) — matches the diff-and-patch interaction primitive.
- Per-repo degradation ("slow repo → that repo 'unavailable', board still renders") in arch + a dedicated `test_cache.py` (line 753) — matches UX-DR8.
- Origin validation over `/mcp` AND `/state`/dashboard routes (lines 238, 744) — matches UX self-contained/localhost posture.
- Architecture explicitly names `DESIGN.md` + `EXPERIENCE.md` as the binding visual/behavioral contract and states "UX-DR1–13 are enumerated there and carried in the epics" (lines 501–506).

### Alignment Issues

**None blocking.** Three-way alignment (UX ↔ PRD ↔ Architecture) is coherent.

### Warnings / Minor Observations

- **W-UX-1 (low — timing budget to verify in implementation):** UX sets poll interval ~1–2s and stale threshold at 2× poll interval; architecture defines worst-case freshness = *background tick interval + poll interval*. To honor the ≤3s SLO (FR-9), the background refresh tick **and** the poll interval must jointly sum to ≤3s (e.g. tick ≈1s + poll ≈1–2s). This is consistent but not pinned to a single number in any document — worth fixing concrete intervals in Story 2.2/2.4b rather than leaving "~1–2s" loose.
- **W-UX-2 (informational):** The browser-free test strategy (HTML-output asserts via an HTML parser, static CSS/JS lint, WCAG-contrast math, `node --test` for `diff()`) is fully specified in the UX spec and epics AR-12, but the architecture's quality-gate prose surfaces it only lightly. Not a gap — coverage exists in the binding documents (epics/UX) the stories trace to.

## Epic Quality Review

Reviewed against create-epics-and-stories standards: user value, epic independence, forward dependencies, story sizing, AC quality, table-creation timing, and starter-template handling.

### Best-Practices Compliance Checklist

| Check | Epic 1 | Epic 2 | Epic 3 |
|-------|--------|--------|--------|
| Delivers user value (not a technical milestone) | ✅ | ✅ | ⚠️ borderline |
| Functions independently (no forward epic dep) | ✅ | ✅ (Epic 1 only) | ✅ (Epic 1 only) |
| Stories appropriately sized | ⚠️ 1.2 large | ✅ | ✅ |
| No forward story dependencies | ✅ | ✅ | ✅ |
| DB tables created when needed | ⚠️ (both in 1.2) | n/a | n/a |
| Clear, testable acceptance criteria | ✅ exemplary | ✅ exemplary | ✅ exemplary |
| Traceability to FRs maintained | ✅ | ✅ | ✅ |

### 🔴 Critical Violations

**None.** No technical-only epic (the foundation/walking-skeleton work is folded *into* the value-delivering Epic 1 rather than split into a standalone "Epic 0: Infrastructure"). No forward dependencies. No epic requires a later epic to function — independence holds: Epic 2 uses only Epic 1 output, Epic 3 hardens the server bootstrapped in Story 1.1 and depends on neither Epic 2 nor future work.

### 🟠 Major Issues

- **M1 — Enforced quality gate deferred to the final story (Story 3.3).** ✅ **RESOLVED 2026-06-22.** The pre-commit hook (`ruff check`, `ruff format --check`, `pytest`) — the architecture's regression gate *in place of CI* (no CI in v1) — was only established in Epic 3's last story, so all of Epic 1 and Epic 2 would have been built before the gate was enforced. **Fix applied:** the gate (AR-12) was moved into **Story 1.1** (new AC + decomposition guidance + AR-12 note, tagged `[AMENDED 2026-06-22c]`); Story 3.3 was reframed to install + full-suite gate confirmation. The gate now guards every subsequent story from the start.

  *Original recommendation (for the record):* move the pre-commit hook installation into Story 1.1 alongside the `uv` scaffold, leaving only `uv tool install` packaging in Story 3.3.

### 🟡 Minor Concerns

- **m1 — Story 1.2 bundles several substrate components** (`run_git()` + two pools, two-table `Store`, slug validation, full error taxonomy, per-repo mutation mutex). It is the single largest story. The pieces are cohesive (shared "safe substrate" deliverable) and each has crisp ACs, so this is defensible, but it is a candidate for a 1.2a/1.2b split (git execution vs. persistence) if it proves too big in one pass. *Confirm sizing at story-grooming.*
- **m2 — Both DB tables created together in substrate Story 1.2**, rather than strictly "each story creates the tables it needs." Acceptable here: the schema is only two tightly-coupled tables (`task` + `task_worktree` with FK CASCADE) and **both are written by the first feature story (1.3 `create_task`)**, so this is "created when first needed," not speculative up-front modeling. Noted for transparency, not remediation.
- **m3 — Epic 3 ("Reliable global install & lifecycle") is partly operational** rather than a pure end-user feature. For a single-developer internal tool the operator *is* the user and "install once, run reliably, no opaque port errors" is genuine user value, so it passes — but it sits closest to the technical-milestone line of the three epics. Its goal statement is framed as a developer outcome, which keeps it valid.
- **m4 — Concrete poll/tick intervals unpinned** (cross-ref **W-UX-1**): to meet the ≤3s freshness SLO (FR-9), Story 2.2's background tick interval + Story 2.4b's poll interval must jointly sum to ≤3s; documents say "~1–2s" without fixing numbers. Pin exact values in those stories.

### Dependency Analysis (forward-reference scan)

- **Within Epic 1:** 1.1→1.2→1.3→1.4→1.5→1.6, strictly forward. Story 1.4's note about derive-on-read recovering crash residue references Epic 2 only as a *non-goal rationale*, not a runtime dependency. ✅
- **Within Epic 2:** 2.1(pure)→2.2→2.3→2.4a→2.4b→2.4c, strictly forward. ✅
- **Within Epic 3:** 3.1→3.2→3.3, strictly forward. ✅
- **Starter template:** Architecture mandates a `uv init --package` foundation (AR-1); **Story 1.1 is the setup-from-scaffold story** (scaffold + walking skeleton in one), satisfying the "Epic 1 Story 1 = set up initial project" requirement. ✅

### Overall Epic Quality Verdict

Epic/story quality is **high** — arguably a model breakdown. ACs are uniformly Given/When/Then, testable, and include error paths, concurrency, transition matrices, and named regression tests. The only actionable structural fix is **M1** (pull the quality-gate hook earlier); everything else is confirm-at-grooming polish.

## Summary and Recommendations

### Overall Readiness Status

✅ **READY** (proceed to implementation; address M1 opportunistically, ideally folded into Story 1.1).

The four planning artifacts — PRD (+addendum), Architecture, Epics/Stories, and UX spec — are complete, mutually consistent, and traceable end-to-end. The 2026-06-22 multi-repo / global-server amendment and the 2026-06-22b four-status-set change are threaded coherently through **all** documents (no document is left on an older model). No critical or blocking issues were found.

### Findings by Severity

| Severity | Count | Items |
|----------|-------|-------|
| 🔴 Critical | 0 | — |
| 🟠 Major | 1 | M1 (quality-gate enforcement deferred to final story) — ✅ **RESOLVED 2026-06-22** |
| 🟡 Minor | 6 | m1 (Story 1.2 size), m2 (tables in substrate story), m3 (Epic 3 operational framing), m4 / W-UX-1 (poll/tick intervals unpinned), W-UX-2 (test strategy light in arch prose) |

### Critical Issues Requiring Immediate Action

**None.** Nothing blocks the start of implementation.

### Recommended Next Steps

1. ~~**Adopt M1 (highest-leverage fix):** move the pre-commit quality-gate hook from Story 3.3 into Story 1.1.~~ ✅ **DONE 2026-06-22** — gate (AR-12) moved into Story 1.1; Story 3.3 reframed to install + full-suite confirmation.
2. **Pin concrete timing numbers (m4 / W-UX-1):** in Story 2.2 (background refresh tick) and Story 2.4b (poll interval), fix exact values whose sum is ≤3s to make the FR-9 SLO testable (e.g. tick 1s + poll 1–2s); align the UX-DR6 stale threshold to the chosen poll interval.
3. **Confirm Story 1.2 sizing at grooming (m1):** decide whether to keep the substrate story whole or split it into git-execution (1.2a) and persistence (1.2b); proceed whole if the team is comfortable with its scope.
4. **Begin with Story 1.1** — the walking skeleton (scaffold + Streamable-HTTP `/mcp` mount + Origin middleware + 127.0.0.1 bind + a no-op tool round-trip). It de-risks the load-bearing transport/security wiring before any feature code, exactly as sequenced.

### Final Note

This assessment reviewed 4 artifact sets across 5 dimensions (document inventory, PRD requirements, epic FR coverage, UX alignment, epic/story quality) and identified **7 issues**: **0 critical, 1 major, 6 minor**. FR coverage is **100% (13/13)**, UX↔PRD↔Architecture alignment is coherent, and epic/story structure follows best practices (user-value epics, strict forward-only dependencies, starter-template-first, exemplary testable ACs). The single major item (M1) is a low-effort sequencing improvement, not a correctness gap. **You may proceed to implementation as-is**; folding M1 into Story 1.1 is strongly recommended and cheap.

---

*Assessment by: Implementation Readiness reviewer (Product Manager role) for Dev · Date: 2026-06-22*
