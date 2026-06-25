---
baseline_commit: cc6c8feb7f4e9ba73c87620a09d867ae6dcf0a7e
---

# Story 2.1: Derive-on-read projection (pure, task-grouped)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer building the tool,
I want a pure function that joins live per-repo git worktree listings with the Store's task records into a task-grouped view,
so that the dashboard view is always a faithful projection of git with no stored derived state to drift.

## Acceptance Criteria

1. **Given** per-repo `git worktree list --porcelain` outputs and the Store's `task` + `task_worktree` rows,
   **When** the projection runs,
   **Then** it returns a `CacheSnapshot` grouped by task (`TaskView` → per-repo `WorktreeView`) with `generated_at`, tasks sorted by `task_id` ASC and worktrees by `repo_path` ASC.

2. **Given** a `task_worktree` link whose `branch` is absent from its repo's porcelain,
   **When** the projection runs,
   **Then** that worktree is emitted with `orphaned: true` AND surfaced in `warnings` as `orphan_link:<task_id>@<repo>:<branch>`; it is never auto-deleted and never auto-`done`.

3. **Given** a worktree present in a repo's git with no matching link,
   **When** the projection runs,
   **Then** it is surfaced as a task-less/untracked entry, not dropped.

4. **Given** the projection function,
   **When** it executes,
   **Then** it performs no writes, no git/DB I/O, and no destructive git op (purity test), and is total — it never throws on orphans or malformed-but-parsed input.

## Tasks / Subtasks

- [x] **Task 1 — Define the three view dataclasses in a NEW `src/dev_helper_mcp/projection.py`** (AC: 1)
  - [x] `@dataclass(frozen=True) WorktreeView` with EXACTLY these fields (pinned shape, architecture.md:376-385): `repo_path: str`, `branch: str`, `path: str | None`, `head: str | None`, `detached: bool`, `locked: bool`, `prunable: bool`, `orphaned: bool`. (Drops `bare` from `WorktreeEntry`; adds `repo_path` + `orphaned`.)
  - [x] `@dataclass(frozen=True) TaskView`: `task_id: str`, `description: str | None`, `status: str | None`, `created_at: str | None`, `updated_at: str | None`, `worktrees: tuple[WorktreeView, ...]` (frozen ⇒ use a tuple, not `list`; field order = architecture.md:369-375).
  - [x] `@dataclass(frozen=True) CacheSnapshot`: `generated_at: str`, `tasks: tuple[TaskView, ...]`, `warnings: tuple[str, ...]` (architecture.md:365-368).
  - [x] Module docstring states: PURE derive-on-read; no `mcp`/`starlette` imports (Invariant 7); no git/DB I/O (Invariant 4 — git porcelain is the existence truth, DB holds only links); imports only `dataclasses`, `typing`, and `git/porcelain.py`'s `WorktreeEntry` + `config.BRANCH_PREFIX`. Snake_case fields = the `/state` JSON contract (Invariant 3); `dataclasses.asdict()` yields the snake_case dict 2.3 will serialize.
- [x] **Task 2 — Implement the pure `project(...)` function** (AC: 1, 2, 3, 4)
  - [x] Signature (recommended, keyword-only): `def project(*, git_listings: Mapping[str, list[WorktreeEntry]], tasks: list[dict], generated_at: str) -> CacheSnapshot`. `git_listings` = `repo_path → parsed porcelain entries` (the caller's per-repo fan-out, Story 2.2). `tasks` = the `store.list_tasks()` shape (rows + nested `worktrees:[{repo_path,branch,worktree_path}]`). `generated_at` is **injected** by the caller (`now_iso()`) — see Decision B; do NOT call `now_iso()` inside (keeps it deterministic/total for the purity test). **Take plain data, never a `Store`/`GitRunner` — the signature itself is the purity guarantee (it cannot do I/O).**
  - [x] Build, per repo `R` in `git_listings`, the present-branch index `present[R] = {e.branch: e for e in git_listings[R] if e.branch is not None}` (detached entries have `branch=None` → excluded; that is the known detached false-positive, see Dev Notes).
  - [x] **Join key is the slug throughout** (the `agent/<slug>` branch). Group all worktree cells by `task_id`/slug into `TaskView`s:
    - [x] **Tracked link cells** — for each `tasks[i].worktrees[j]` link `(repo_path, branch, worktree_path)`: if `repo_path in git_listings` AND `branch in present[repo_path]` → matched: `orphaned=False`, fill `path/head/detached/locked/prunable` from that porcelain entry. Else (repo absent from `git_listings`, OR branch absent from its porcelain) → `orphaned=True`, `path=None`, `head=None`, `detached=False`, `locked=False`, `prunable=False`, AND append `orphan_link:<task_id>@<repo_path>:<branch>` to warnings (AC2).
    - [x] **Untracked cells (AC3)** — for each repo `R` and each porcelain entry whose `branch` is **`agent/`-prefixed** (Decision A) and is **not** claimed by any link for `(R, branch)`: emit a `WorktreeView` (`orphaned=False`, fields from porcelain) grouped under the synthetic slug `branch.removeprefix("agent/")`. Non-`agent/` worktrees (the repo's own `main`/`master` checkout, user branches) are ignored — they are not this tool's artifacts.
  - [x] Each `TaskView`'s task-level fields (`description/status/created_at/updated_at`) come from the matching DB `task` row if one exists for that slug, else `None` (task-less / untracked-only slug).
  - [x] Sort: `tasks` by `task_id` ASC; each task's `worktrees` by `repo_path` ASC; `warnings` sorted (deterministic output — AC1/AC4). Return all as tuples (frozen, immutable, swapped-whole).
- [x] **Task 3 — Author `tests/test_projection.py`** (AC: 1, 2, 3, 4)
  - [x] **Purity / totality (AC4):** construct `WorktreeEntry(...)` objects + task dicts directly (NO git, NO Store, NO `run_git` — this story's tests have zero git-safety surface). Assert `project(...)` returns without raising on orphan links, untracked entries, detached (`branch=None`) entries, empty inputs, a task with zero links, and a link whose repo is absent from `git_listings`. Assert determinism: two calls with identical inputs (same `generated_at`) produce equal snapshots.
  - [x] **Grouping + ordering (AC1):** a two-repo task → ONE `TaskView` with two `WorktreeView`s sorted by `repo_path`; multiple tasks sorted by `task_id`; task-level fields copied from the row; `generated_at` is the injected value verbatim.
  - [x] **Orphan (AC2):** a link whose branch is absent from its repo's porcelain → that `WorktreeView.orphaned is True`, `path is None`, and `warnings` contains exactly `orphan_link:<task_id>@<repo>:<branch>`; the task/links are still present (never dropped, never status-mutated). A fully-orphaned task (all links orphaned) is surfaced, not dropped.
  - [x] **Untracked (AC3):** an `agent/<slug>` porcelain entry with no link → a task-less `TaskView` (slug = stripped branch, `status/description/...=None`) carrying that worktree, `orphaned=False`. A non-`agent/` porcelain entry (e.g. `master`) is NOT surfaced (Decision A).
  - [x] (Optional, strengthens AC4) assert `dataclasses.asdict(snapshot)` is an all-snake_case nested dict with the pinned key set — pre-wires the 2.3 `/state` contract.
- [x] **Task 4 — Confirm the seam + gate** (AC: 4)
  - [x] `projection.py` is already in scope of `tests/test_adapter_seam.py`'s core scan (it AST-scans `core/`, `git/`, and `store`/`projection`/`cache`); confirm it adds no forbidden `from mcp …`/`from starlette …` import and the seam test stays green.
  - [x] Full gate green: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dependency, no `slow` test, no schema change, no `/state`/cache/git code.

### Review Findings

_Code review 2026-06-25 — three adversarial layers (Blind Hunter + Edge Case Hunter + Acceptance Auditor), no layer failed. **All 4 ACs, both Decisions (A/B), and Invariants 1/3/4/7/10/11 confirmed satisfied** by implementation and tests. No High and no reachable Medium defects. 4 patch items (low-severity hardening + test strengthening); 8 findings dismissed as within-contract, spec-pinned, git-unreachable, or by-design (see notes below)._

- [x] [Review][Patch] Guard empty-slug untracked branch — skip a porcelain entry whose branch is exactly `agent/` (stripped slug == `""`) so it cannot emit a phantom `TaskView(task_id="")` into the snapshot/`/state` payload. Serves AC4 totality at the typed-input boundary. [src/dev_helper_mcp/projection.py:157-177] — Fixed: added `if not slug: continue`; test `test_bare_agent_branch_does_not_emit_phantom_empty_slug_task`.
- [x] [Review][Patch] Dedup untracked emission + total worktree sort — record emitted `(repo_path, branch)` so a duplicate unclaimed `agent/<slug>` entry in one repo is not double-emitted, and sort each task's worktrees by `(repo_path, branch)` (not `repo_path` alone) so order is total/contract-pinned rather than insertion-order-by-luck. [src/dev_helper_mcp/projection.py:157-190] — Fixed: `claimed.add((repo_path, branch))` on untracked emit + sort key `(w.repo_path, w.branch)`; test `test_duplicate_unclaimed_agent_entry_not_double_emitted`.
- [x] [Review][Patch] Strengthen determinism test — build two `git_listings`/`tasks` with identical contents in *different insertion orders* and assert equal snapshots; the current test reuses the same objects, so it cannot catch input-order dependence (the property the docstring claims). [tests/test_projection.py] — Fixed: replaced with `test_projection_is_independent_of_input_insertion_order`.
- [x] [Review][Patch] Assert `/state` serialisability — add a `json.dumps(dataclasses.asdict(snap))` round-trip to the asdict test, backing the docstring's "payload 2.3 serialises with no translation layer" claim (current test asserts key names only). [tests/test_projection.py] — Fixed: added `json.dumps` round-trip assertion to `test_asdict_is_all_snake_case_with_pinned_keys`.

_Dismissed (real but not actionable now): (1) duplicate branch in one repo collapses on the tracked join — git forbids two worktrees on one branch; "last wins" acceptable. (2) duplicate `task_id` in `tasks` — `store.list_tasks()` groups by PK, cannot occur. (3) `orphan_link:…@…:…` warning ambiguous if a field holds `@`/`:` — the format is pinned exactly by AC2 and is human-facing, not machine-parsed. (4) hard-indexing of join keys vs `.get()` for display fields — intentional (mandatory keys are caller-guaranteed; AC4 totality targets git-state weirdness, not malformed caller dicts). (5) warnings not deduplicated — links are PK-unique upstream. (6) `WorktreeView.branch` non-nullable typing note — by design (detached entries filtered before emission). (7) `tasks: list[dict]` loose typing — matches the spec signature. (8) untracked+orphan same-slug merge untested — by-design Decision-A grouping._

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
2.1 is the **first Epic 2 story** and ships exactly ONE NEW pure module: `src/dev_helper_mcp/projection.py` + `tests/test_projection.py`. It is the foundation the rest of Epic 2 stacks on (cache → `/state` → UI). The traps:
- **Build only the pure function + view dataclasses. NOTHING else.** No `cache.py`, no background refresher, no `/state` route, no dashboard, no git fan-out, no `run_git`, no `Store` call. `project(...)` *receives* already-parsed porcelain (`list[WorktreeEntry]`) and already-fetched DB rows as plain data; the caller that does the git/DB I/O is **Story 2.2** (`cache.py`). [Source: epics.md:355-379 (2.2 owns the fan-out/cache); architecture.md:741-742, 350-406]
- **Purity is the headline AC (AC4), not a nice-to-have.** The function does no I/O and never throws on parsed-but-weird input. The strongest enforcement is the **signature**: it takes plain dicts/dataclasses, never a `Store` or `GitRunner`, so it is structurally incapable of I/O. Keep it that way.
- **`generated_at` is INJECTED, not read from the clock inside the function.** This is what makes the purity/idempotency test deterministic and keeps the single-`now_iso()` discipline at the caller boundary. See Decision B.
- **No git in this story's tests.** Unlike 1.3–1.6, 2.1 tests construct `WorktreeEntry` objects + task dicts by hand — there is no git spawn, no Store, no `tmp_git_repo` needed. (The git-safety HARD RULE still stands repo-wide, but 2.1 simply has no git surface to trip it.)
- **DB schema unchanged. No migration.** The projection reads the *output* of `store.list_tasks()`; it does not query, and adds no SQL.

### ⚠️ Decision A — scope & grouping of "untracked" worktrees (CONFIRM WITH OPERATOR)
AC3 says "a worktree present in a repo's git with no matching link → surfaced as task-less/untracked, not dropped," and architecture.md:386-391 says the same with "(or a dedicated `untracked` bucket)." But `git worktree list --porcelain` for a tracked repo returns **the repo's own main checkout** (on `master`/`main`) and any user worktrees **in addition** to our `agent/<slug>` worktrees. Surfacing *every* unlinked worktree would put each repo's main checkout on the **task** board as a phantom card — almost certainly not intended, and noise that fights the glanceable-board UX (Epic 2.4a).
- **Recommended default (the story is written assuming this):** an unlinked worktree becomes an untracked entry **only when its branch is in the `agent/` namespace** (`config.BRANCH_PREFIX`). Rationale: this tool *only ever* creates `agent/<slug>` worktrees, so an unlinked `agent/<slug>` worktree is exactly the **crash-orphan** the derive-on-read recovery story wants visible (a SIGKILL'd `create_task` leaves a worktree with no DB row — architecture.md:439-443); the main checkout and user branches are not this tool's artifacts and are ignored. **Grouping:** untracked agent worktrees group by their slug (`branch.removeprefix("agent/")`) into synthetic task-less `TaskView`s — so a crash-orphaned multi-repo task shows as ONE recoverable card, mirroring how real tasks group by slug across repos.
- **Alternative (literal):** emit ALL unlinked worktrees (incl. main checkouts) under a dedicated `untracked` bucket. Higher fidelity to the literal text, much noisier board, and pushes the `agent/`-filter policy downstream into the UI.
- **This is the one open question for the operator (see end of run).** Pick one before implementing; the story assumes the `agent/`-namespace filter. (Mirrors how Story 1.6 flagged its Decision A and proceeded on the recommended option.)

### Decision B — `generated_at` is a parameter (specified, low-contention)
The pinned shape says `CacheSnapshot.generated_at = now_iso()` (architecture.md:366). For a **pure, deterministic, total** function (AC4) the value is **passed in** (`generated_at: str`), and the caller (`cache.py`, Story 2.2 — the rebuild step in the mutation critical section, architecture.md:398) stamps it with `now_iso()` (util.py:6, Invariant 11). Calling `now_iso()` *inside* `project()` would make every test non-deterministic and is the kind of hidden clock-read the purity AC is meant to forbid. Encoded into the recommended signature; flag at run-end only if the operator prefers the function own the clock.

### The exact join algorithm (the heart of the story)
The **slug** (the `agent/<slug>` branch, identical across every repo a task spans — architecture.md:331, util.py:80) is the single join key. Per architecture.md:386-391 "porcelain is the existence set; `task_worktree` links LEFT-JOIN on `(repo_path, branch)`":
1. For each repo `R` in `git_listings`: `present[R] = {e.branch: e for e in entries if e.branch is not None}`.
2. **Tracked link → WorktreeView:** for each link `(task_id, repo_path, branch=agent/slug, worktree_path)` from `tasks[*].worktrees`:
   - branch present in `present[repo_path]` → **matched**: `orphaned=False`; copy `path/head/detached/locked/prunable` from the porcelain entry.
   - repo absent from `git_listings`, OR branch absent from `present[repo_path]` → **orphaned**: `orphaned=True`, `path=None`, `head=None`, `detached=False`, `locked=False`, `prunable=False`; append `orphan_link:<task_id>@<repo_path>:<branch>` to warnings.
3. **Untracked → WorktreeView (Decision A):** for each repo `R`, each porcelain entry with an `agent/`-prefixed branch NOT claimed by a link for `(R, branch)` → `orphaned=False`, fields from porcelain, grouped under slug `branch.removeprefix("agent/")`.
4. **Group by slug → TaskView:** task-level fields from the DB `task` row if the slug has one, else `None`. `worktrees` sorted by `repo_path` ASC.
5. **Snapshot:** `tasks` sorted by `task_id` ASC; `warnings` sorted; `generated_at` injected. All tuples, immutable, swapped whole.

**The orphan link is NEVER auto-deleted and NEVER auto-`done`** (AC2; Invariant 4 / architecture.md:344-348) — the projection only *reports*; it has no mutation capability by construction.

### What the substrate already gives you (verified shipped 1.1–1.6 — reconcile against THIS, not the architecture pseudo-code)
- **`git/porcelain.py`** — `@dataclass(frozen=True) WorktreeEntry(path, branch, head, detached, locked, prunable, bare)` + `parse_worktree_porcelain(raw: bytes) -> list[WorktreeEntry]`. `branch` is already `refs/heads/`-stripped (so `agent/foo`, `master`) and is `None` on detached HEAD. The parser is **total** (already handles malformed/odd input) — your projection consumes its dataclass output, never raw bytes. **This is your input element type. Do NOT re-parse porcelain in projection.** [Source: git/porcelain.py:31-89]
- **`store.list_tasks(status=None, repo=None)`** (store.py:286-328) — returns `[{task_id, description, status, created_at, updated_at, worktrees:[{repo_path, branch, worktree_path}, …]}]`, already grouped + sorted by `task_id`/`repo_path`. **This is the DB-side input shape** the caller (2.2) will hand `project()` (called with no filter for the full snapshot). Your tests can hand-build the same dict shape. Note it does NOT include git-derived worktree *existence* — that is precisely what the projection joins in. [Source: store.py:286-328]
- **`util.now_iso()`** (util.py:6-14) — UTC ISO-8601 `Z`, second precision; the single timestamp helper. The **caller** uses it for `generated_at`; the projection does not import it (Decision B).
- **`config.BRANCH_PREFIX`** = `"agent/"` (config.py:80) + `branch_name_for(slug)`/`worktree_path_for(repo, slug)` helpers. Use `BRANCH_PREFIX` for the Decision-A namespace filter and the slug strip — no magic `"agent/"` literal in `projection.py` (Invariant: all tunables in `config.py`). [Source: config.py:76-97]
- **`@dataclass(frozen=True)` precedent** — `WorktreeEntry` (porcelain.py:31). Mirror it for the three view types: frozen ⇒ immutable snapshot (architecture.md:391 "immutable and swapped whole") and ⇒ `worktrees`/`tasks`/`warnings` are **tuples**, not lists.
- **`tests/test_adapter_seam.py`** already AST-scans `projection` for forbidden imports (project-context.md:32). Adding `from mcp …` to `projection.py` is an automatic gate failure — keep it pure-Python + `git/porcelain` + `config`.

### Binding invariants (architecture.md §Invariants:63-76; project-context.md)
- **Invariant 4 — Derive-on-read; never persist derived state.** Git porcelain (per tracked repo) is the sole truth for worktree existence; the DB holds only task records + `(repo_path, branch, worktree_path)` links; the view is *recomputed*, never stored. `project()` is the recompute. [architecture.md:68, 344-348, 350-360]
- **Invariant 7 — SDK seam:** `projection.py` imports no `mcp`/`starlette` (auto-policed). Pure core module. [architecture.md:71; project-context.md#SDK-isolation seam]
- **Invariant 3 — all JSON keys snake_case** (this is the `/state` payload, 2.3): dataclass field names ARE the contract — keep them snake_case so `dataclasses.asdict()` is the payload with no translation layer. [architecture.md:67]
- **Invariant 11 — timestamps via `now_iso()` only** — applied at the caller for `generated_at` (Decision B). [architecture.md:75]
- **Invariant 1 / 10 — no git here at all**, so the "single `run_git()`" and "no destructive op on read path" rules are satisfied vacuously; the projection cannot shell out (no I/O). Stated so a reviewer can check it by the absence of any git import. [architecture.md:65, 74]

### Critical gotchas (carry into implementation)
- **Detached-HEAD agent worktree reads `orphaned:true` (known false positive).** A worktree in detached HEAD parses to `branch=None`, so a stored `agent/<slug>` link won't find it in `present[R]` → flagged orphaned even though it exists on disk. This is the **already-deferred** 1.5 limitation (deferred-work.md#story-1-5 "Detached-HEAD agent worktree reads orphaned"), not a 2.1 bug — agent worktrees are created on `agent/<slug>` and don't normally detach. Do NOT try to reconcile by path here (path reconciliation is an explicit v1 non-goal). Just don't crash on `branch=None` (totality).
- **A vanished/absent repo's links → orphaned, not an error.** If a link's `repo_path` is not a key in `git_listings` (repo deleted, or simply not fanned out this tick), treat its branch as absent → `orphaned=True` + warning. Total, no throw. (Consistent with 1.5's vanished-repo deferral; the projection *surfaces* it, never cleans it.)
- **Slug-vs-task_id are the same string.** The DB `task_id` IS the slug, and links carry `branch = agent/<task_id>` (util.py:80, architecture.md:331). So `link.branch.removeprefix("agent/") == link.task_id` for real links — group tracked cells by `task_id` directly; only *untracked* (link-less) cells need the `removeprefix` to recover a synthetic slug. Don't over-engineer a separate key.
- **Frozen dataclass ⇒ no in-place mutation.** Build child lists locally, then freeze into tuples at construction. `worktrees=tuple(sorted(views, key=lambda w: w.repo_path))`.
- **Determinism:** sort `warnings` and `tasks`/`worktrees` explicitly. AC1 pins task/worktree order; AC4's idempotency needs `warnings` order stable too. Don't rely on dict insertion order for the public output.
- **Empty / zero cases are valid, not errors:** empty `git_listings` + empty `tasks` → `CacheSnapshot(generated_at, (), ())`. A task with zero links → `TaskView` with empty `worktrees`. Never raise.
- **No camelCase, no `bare` in the view.** `WorktreeView` deliberately omits porcelain's `bare` and renames nothing — exactly the 8 pinned fields.

### 🛑 Git safety in tests — HARD RULE (read, but note 2.1 has no git surface)
Repo-wide rule (project-context.md#Git safety in tests; conftest.py autouse `_guard_project_repo_untouched`; test_git_safety.py AST scan): every test git op targets a `tmp_path` repo, never this one. **Story 2.1 spawns NO git** — `project()` is pure and its tests build `WorktreeEntry`/dict inputs by hand — so there is no git-safety surface to manage. Do not introduce `tmp_git_repo` or `create_task` calls into `test_projection.py`; that would add a real git surface for a function that, by design, never touches git. (The live fan-out that *does* spawn git is Story 2.2, where `tmp_git_repo` returns.)

### Previous-story (1.6 / Epic 1) intelligence that applies directly
- **"This file wins over architecture pseudo-code."** 1.6's Dev Notes hammered that `list_tasks` is a Store read, NOT the Epic 2 view — and explicitly deferred the live-git/cache/orphan view to **this** story. You are now building the deferred piece. Reconcile against the **shipped** `store.list_tasks()` shape (store.py:286-328) and `WorktreeEntry` (porcelain.py:31), not the architecture's prose. [Source: 1-6 story Dev Notes#Scope boundaries; project-context.md#Usage Guidelines]
- **Test style proven 1.2–1.6:** plain `pytest` functions, async (where needed) via `asyncio.run()`, no `pytest-asyncio`, no new dep, no `slow` test. 2.1's tests are even simpler — **fully synchronous** (the function is sync and pure). [Source: 1-6 story §Testing standards]
- **Dataclass + keyword-only-args core style** (worktrees.py / porcelain.py): keyword-only injected inputs, frozen dataclasses for value objects, plain returns. Mirror it.
- **The two-coexisting-"closed" semantics (1.6):** `done` keeps the `task` row + links (folded into `✓ N done` on the board); `remove_worktree` of the last worktree DELETEs the row. **Implication for the projection:** a `done` task still has DB rows, so it still appears as a `TaskView` (with `status="done"`) — the projection does NOT filter out `done`; the *UI* (2.4a) folds it. Surface everything; let the view consumer decide visibility. [Source: 1-6 story Dev Notes#Two coexisting "closed" semantics]

### Git / recent-work intelligence
- **Baseline commit `cc6c8fe`** ("1-6 complete"). Epic 1 is fully implemented + reviewed-`done` (1.1–1.6); tool surface is the final **5** (`create_task`, `list_worktrees`, `remove_worktree`, `update_task`, `list_tasks`). Epic 2 starts here with zero prior Epic-2 code.
- **Src tree present** (find src): `core/{tasks,worktrees,mutator,slug}.py`, `git/{runner,repo_lock,porcelain}.py`, `store.py`, `tools/{handlers,models}.py`, `server_factory.py`, `middleware.py`, `cli.py`, `server.py`, `config.py`, `util.py`, `errors.py`. **`projection.py` and `cache.py` do NOT exist yet** — 2.1 creates `projection.py` (only). [Source: find src; architecture.md:741-742]
- Commit cadence is one commit per story after a green gate + adversarial code-review (`3c41a67`→`cc6c8fe`); 2.1 follows the same. No `errors.py` change expected (the projection raises nothing — it is total).

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100 / `target-version=py314`, `src/` layout; type hints on every public signature (`str | None`, `tuple[X, ...]`, `Mapping[str, list[WorktreeEntry]]`). `from __future__ import annotations` at the top (matches porcelain.py/store.py). [project-context.md#Technology Stack]
- **No SDK, no async, no DB, no git** in this module → no `mcp`/`aiosqlite`/`asyncio` import. Pure stdlib (`dataclasses`, `collections.abc.Mapping`/`typing`) + `git/porcelain.WorktreeEntry` + `config.BRANCH_PREFIX`. **No new dependency.**
- `dataclasses.asdict()` recurses into nested frozen dataclasses and tuples → gives the snake_case nested dict 2.3's `/state` will `json.dumps`. (Tuples serialize as JSON arrays — fine.)

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/projection.py` (top-level package module, alongside `store.py` — NOT under `core/`; it is "core logic layer" by boundary, but its file lives at the package root per architecture.md:741). `tests/test_projection.py`.
- **UPDATE:** none expected. (`config.BRANCH_PREFIX` already exists; `store.list_tasks` already exists; `WorktreeEntry` already exists. If the operator picks Decision-A "literal/all-worktrees", still no `config` change — the filter simply widens.)
- **UNCHANGED (do not edit):** all of `core/`, `git/`, `store.py`, `tools/`, `server_factory.py`, `middleware.py`, `cli.py`, `server.py`, `errors.py`, `util.py`, `config.py`. **DB schema unchanged — no migration.**
- **DEFERRED, do NOT create or pull forward:** `cache.py`, the background refresher, the per-repo git fan-out, `/state`, the dashboard, any reconciliation/cleanup of orphans, path-based reconciliation. Those are Stories 2.2 / 2.3 / 2.4a-c. [Source: epics.md:355-474; architecture.md:741-742, 793; deferred-work.md]
- Test mirrors src: `tests/test_projection.py` (the architecture's planned test file, architecture.md:756 — "purity (no writes), orphan detection, idempotent view, multi-repo grouping by task").

### Testing standards
- `tests/test_projection.py`, synchronous plain `pytest` functions (no `asyncio`, no Store, no git). Build inputs from `WorktreeEntry(...)` + dict literals.
- **Coverage to the four ACs:** (1) grouping + `task_id`/`repo_path` ordering + `generated_at` passthrough; (2) orphan link → `orphaned:true` + `path:None` + exact `orphan_link:…` warning + not-dropped/not-mutated + fully-orphaned task surfaced; (3) untracked `agent/<slug>` → task-less `TaskView`, and a non-`agent/` worktree NOT surfaced (Decision A); (4) purity/totality — no throw on orphans/detached/empty/absent-repo, deterministic across two identical calls, and (optional) `asdict()` is all-snake_case with the pinned keys.
- Everything green under the enforced gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"`. No new dep, no `slow` test, no schema migration. The `tests/test_adapter_seam.py` core scan must stay green for the new module. [Source: project-context.md#Testing rules, #Code-quality gate; architecture.md:756]

### References
- [Source: epics.md#Story 2.1 (lines 331-353)] — user story + all 4 BDD ACs verbatim (CacheSnapshot grouped by task, sort orders, orphan-link + warning, untracked-not-dropped, purity + totality).
- [Source: epics.md:327-329] — Epic 2 intent: derive-on-read, pure projection over per-repo git × task records, "a poll never shells out to git", forward-only story order (pure projection → cache/refresher → endpoint → UI). Epic 1 does not depend on Epic 2.
- [Source: epics.md:355-474] — Stories 2.2 (cache + background refresher owns the git fan-out + atomic ref swap), 2.3 (`/state`), 2.4a-c (UI) — the scope fence for what 2.1 must NOT build.
- [Source: architecture.md:362-391] — the pinned `CacheSnapshot`/`TaskView`/`WorktreeView` shape (field names + types + sort orders) and the LEFT-JOIN-on-`(repo_path, branch)` join rule, orphan + untracked emission, "immutable and swapped whole".
- [Source: architecture.md:344-348] — orphaned-link rule: shown, flagged orphaned, NEVER auto-deleted / auto-`done`; a fully-orphaned task is surfaced, not dropped.
- [Source: architecture.md:350-360, 393-406] — derive-on-read model + the mutation critical-section rebuild step (the future caller of `project()`); `generated_at` carried on the snapshot.
- [Source: architecture.md:63-76 (Invariants 3, 4, 7, 11)] — snake_case keys, derive-on-read, SDK seam, `now_iso()` timestamps.
- [Source: architecture.md:439-443] — crash-safety non-goal → orphaned/untracked worktrees surfaced by derive-on-read for operator recovery (the rationale behind the AC3 untracked behavior + Decision A).
- [Source: architecture.md:741-742, 765-781] — `projection.py` location + boundary: "PURE derive-on-read: (git_listing, annotations) → view; no writes"; pure, no I/O; `cache.py` is the only writer of the in-memory view.
- [Source: src/dev_helper_mcp/git/porcelain.py:31-89] — `WorktreeEntry` dataclass + total parser (the input element type; `branch` stripped, `None` on detached).
- [Source: src/dev_helper_mcp/store.py:286-328] — `list_tasks()` output shape (the DB-side input the caller hands `project()`).
- [Source: src/dev_helper_mcp/config.py:76-97] — `BRANCH_PREFIX="agent/"`, `branch_name_for`, `worktree_path_for` (slug↔branch relationship; the Decision-A namespace).
- [Source: src/dev_helper_mcp/util.py:6-14] — `now_iso()` (used by the caller for `generated_at`, Decision B).
- [Source: project-context.md] — SDK-isolation seam (projection in the core scan), snake_case/derive-on-read contract, `now_iso()` discipline, git-safety-in-tests, "this file wins over architecture pseudo-code".
- [Source: deferred-work.md#story-1-5] — detached-HEAD-reads-orphaned + vanished/moved-repo link limitations: known, deferred (reconciliation is a v1 non-goal); the projection surfaces, never reconciles.
- [Source: 1-6-update-and-list-tasks-status-lifecycle-and-complete-the-tool-surface.md] — `list_tasks` is the Store read and the live view was explicitly deferred to Epic 2 (this story); two-coexisting-closed semantics ⇒ `done` tasks still surface in the projection.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Claude Opus 4.8, 1M context)

### Debug Log References

- RED: `uv run pytest tests/test_projection.py` → `ModuleNotFoundError: dev_helper_mcp.projection` (tests authored before module).
- GREEN: `uv run pytest tests/test_projection.py -q` → 18 passed.
- Gate: `uv run ruff check .` → all checks passed; `uv run ruff format --check .` → flagged `tests/test_projection.py`, applied `ruff format` (1 file reformatted), re-check clean.
- Seam: `uv run pytest tests/test_adapter_seam.py -q` → green (no `mcp`/`starlette` import in `projection.py`).
- Regression: `uv run pytest -m "not slow" -q` → 188 passed, 1 deselected (the `slow` uvicorn smoke test).

### Completion Notes List

- Implemented the single new pure module `src/dev_helper_mcp/projection.py` with the three pinned frozen dataclasses (`WorktreeView` — 8 fields, drops `bare`, adds `repo_path`+`orphaned`; `TaskView`; `CacheSnapshot`) and the pure `project(*, git_listings, tasks, generated_at)` function. Purity is structural: the signature takes plain dicts/`WorktreeEntry` and `generated_at` is injected (Decision B) — it has no `Store`/`GitRunner`/clock to do I/O. Imports limited to `dataclasses`, `collections.abc.Mapping`, `config.BRANCH_PREFIX`, and `git/porcelain.WorktreeEntry`.
- **Decision A — resolved on the recommended default.** Untracked worktrees are surfaced ONLY when their branch is in the `agent/` namespace (`config.BRANCH_PREFIX`), grouped by stripped slug into synthetic task-less `TaskView`s; the repo's own `master`/`main` checkout and user branches are ignored so they don't become phantom cards. (Flagged for operator at run-end; the literal "all unlinked worktrees" alternative was not taken.)
- Join key is the slug throughout. Tracked links LEFT-JOIN on `(repo_path, branch)` vs the per-repo present-branch index (`branch is not None`); unmatched → `orphaned=True` + sorted `orphan_link:<task_id>@<repo>:<branch>` warning, never dropped/mutated. Linked `(repo, branch)` pairs are recorded in a `claimed` set so the untracked pass never double-emits them.
- Totality verified: no throw on orphan links, detached (`branch=None`) entries, empty inputs, zero-link tasks, or links into an absent repo. Determinism verified: two identical calls produce equal snapshots; `tasks`/`worktrees`/`warnings` all explicitly sorted; outputs are tuples (immutable, swap-whole). `dataclasses.asdict()` confirmed all-snake_case with the pinned key set (pre-wires the 2.3 `/state` contract).
- Scope held: no `cache.py`, no `/state`, no git fan-out, no `run_git`, no `Store` call, no DB schema change, no new dependency, no `slow` test. SDK seam stays green.
- **Post-review hardening (4 patches applied):** (1) empty-slug guard — a bare `agent/` branch no longer emits a phantom `TaskView("")`; (2) untracked emission now records `(repo_path, branch)` in `claimed` so a duplicate unclaimed entry isn't double-emitted; (3) worktrees sort on `(repo_path, branch)` for a total order; (4) two tests strengthened (insertion-order-independent determinism; `json.dumps` round-trip on `asdict`). Two new guard tests added. All four ACs reconfirmed; 8 review findings dismissed as within-contract / spec-pinned / git-unreachable / by-design. Final gate: 190 fast tests pass.

### File List

- `src/dev_helper_mcp/projection.py` (NEW)
- `tests/test_projection.py` (NEW)
- `_bmad-output/implementation-artifacts/2-1-derive-on-read-projection-pure-task-grouped.md` (story tracking: frontmatter `baseline_commit`, task checkboxes, Dev Agent Record, Status)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status: ready-for-dev → in-progress → review)

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-24 | Story 2.1 drafted (ready-for-dev): pure, task-grouped derive-on-read `projection.py` + `CacheSnapshot`/`TaskView`/`WorktreeView` dataclasses + `tests/test_projection.py`. Open Decision A (untracked-worktree scope/grouping — recommend `agent/`-namespace filter, group by slug). Decision B specified (`generated_at` injected for purity). |
| 2026-06-25 | Story 2.1 implemented (review): added pure `projection.py` (3 frozen dataclasses + `project()`) and `tests/test_projection.py` (18 tests, all ACs). Decision A resolved on the recommended `agent/`-namespace filter (grouped by slug). Full gate green: ruff check/format clean, seam green, 188 fast tests pass. No new dep, no schema change, no `slow` test. |
| 2026-06-25 | Code review (3 adversarial layers) → all 4 ACs confirmed, no High/Med reachable defects. Applied 4 patches: empty-slug `agent/` guard, untracked-emission dedup + total `(repo_path, branch)` worktree sort, insertion-order-independent determinism test, `json.dumps` serialisability assertion. 8 findings dismissed (within-contract/spec-pinned/git-unreachable/by-design). Gate green: 190 fast tests pass. Status → done. |
