---
baseline_commit: b236b90563c5c6da8de787b1ea6a5ade8ca13830
---

# Story 3.3: Daily-use install (quality gate from Story 1.1 confirmed at full scope)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want to install the tool once as a console command, with the quality gate (established in Story 1.1) confirmed to cover the complete v1 suite,
so that it is a trustworthy daily tool and regressions are caught even without CI.

## Acceptance Criteria

1. **Given** the project (`src/` layout, `uv_build` backend),
   **When** I run `uv tool install` (or pipx),
   **Then** a `dev-helper-mcp` console entry point is installed and runnable from any directory.

2. **Given** the enforced pre-commit hook **established in Story 1.1** (it was NOT deferred to this story),
   **When** the full v1 suite exists (Epics 1–3 stories complete),
   **Then** the hook still enforces `ruff check`, `ruff format --check`, and `pytest` (the in-process ASGI suite; the real-port uvicorn smoke test may be slow-marked/opt-in) on every commit — this story **confirms the gate scales to the complete suite**, it does not introduce it.

3. **Given** the test suite,
   **When** a CI runner is later introduced,
   **Then** the suite runs unchanged (it is CI-ready).

4. **Given** the running server,
   **When** it logs,
   **Then** it writes stdlib `logging` to stderr at a level set by `DEV_HELPER_LOG` (default `INFO`), sufficient to diagnose a failed tool call or an orphaned link, never logging secrets or full annotation contents at `INFO` (NFR-Observability).

## ⛔ HARD PREREQUISITE — read before anything else

**Story 3.3 is the final Epic 3 / v1 story. It is primarily a confirmation + packaging + logging-audit story** — it ships very little new behavior. It should run after 3.1 and 3.2 so "the full v1 suite (Epics 1–3 stories complete)" (AC2) is literally true.

- **Story 1.1** already bootstrapped the quality gate AND `pyproject.toml`'s `[project.scripts] dev-helper-mcp = "dev_helper_mcp.cli:main"`, `uv_build` backend, `requires-python >= 3.14`, and `cli._configure_logging` (`DEV_HELPER_LOG`, default INFO, stderr). **3.3 confirms and lightly hardens these — it does not introduce them.**
- **The gate was NOT deferred to 3.3** (amendment 2026-06-22c moved it into 1.1 so it guarded Epics 1–2). 3.3 only confirms it scales to the whole suite.

## 🚨 CRITICAL — reconcile AC2 with the gate's REAL current state (read before Task 2)

AC2's literal text says the pre-commit hook enforces `ruff check` + `ruff format --check` + `pytest`. **That is no longer the live configuration.** Per an **operator decision (2026-06-25, recorded in Story 2.4c)**, `.githooks/pre-commit` was deliberately reduced to **`ruff` only**; the test portion of the gate (`uv run pytest -m "not slow"` and `node --test tests/js/`) is run **manually** by the developer. "**This file / the operator decision wins over the architecture's literal pseudo-code**" (project-context.md#Usage Guidelines).

**Therefore the dev agent MUST NOT silently restore `pytest` to the pre-commit hook to "satisfy" AC2.** This is the one genuine spec-vs-reality conflict in Epic 3 and it needs an explicit operator decision — see **Decision A** below. The likely-correct framing of AC2 is: *the full gate (ruff-in-hook + the manual `pytest -m "not slow"` + `node --test tests/js/`) stays green over the complete Epics 1–3 suite, and is CI-ready* — not "re-arm pytest in the hook". **Confirm with the operator before changing `.githooks/pre-commit`.**

## Tasks / Subtasks

- [x] **Task 1 — Confirm the daily-use install (AC: 1)**
  - [x] Verify `pyproject.toml` already exposes `[project.scripts] dev-helper-mcp = "dev_helper_mcp.cli:main"` (it does, from Story 1.1) and `[build-system] build-backend = "uv_build"`, `requires-python = ">=3.14"`, deps `mcp>=1.28,<2` + `aiosqlite>=0.22.1`. **Confirmed unchanged — no drift.**
  - [x] **End-to-end install smoke (manual / opt-in, document it):** documented in the README "Install" section (`uv build` → `uv tool install dist/…whl`, or `uv tool install .` / `pipx install .`; `dev-helper-mcp` runs from any dir; `python -m dev_helper_mcp` equivalent). Kept as a documented manual step, NOT a default-suite test (a full `uv tool install` is heavy + environment-mutating).
  - [x] **README install section:** added an "Install" section (`uv tool install` / pipx, `dev-helper-mcp`, `dev-helper-mcp --port N`, `dev-helper-mcp stop`). The stale "1.1 walking skeleton / no real git/DB/tools/dashboard / ping" copy was replaced with the real v1 description.
- [x] **Task 2 — Confirm the quality gate scales to the full v1 suite (AC: 2)** — *Decision A resolved: operator chose "keep hook ruff-only" (2026-06-29)*
  - [x] Ran the complete gate over Epics 1–3: `ruff check .` + `ruff format --check .` + `pytest -m "not slow"` (314 passed) **and** `node --test tests/js/` (53 passed), plus `pytest -m slow` (10 passed — real-port smoke + 3.1/3.2 lock/lifecycle). **All green = the gate scales.**
  - [x] **`.githooks/pre-commit` left UNCHANGED (ruff-only).** Operator sign-off (Decision A, 2026-06-29) = keep the hook ruff-only; tests stay manual. AC2 satisfied by the green full manual gate + a README "Quality gate (the real split)" note documenting hook = ruff / tests = manual. Also surfaced (operator declined for this story): the hook runs `ruff format .` rather than `ruff format --check .` — left as-is per operator choice.
  - [x] Confirmed the dashboard browser-free tests hold at full scope (HTML-output asserts, static CSS/JS lint, WCAG-contrast math, `node --test` poller `diff()`/patch) and `tests/test_adapter_seam.py` is green.
- [x] **Task 3 — CI-readiness (AC: 3)**
  - [x] Confirmed the suite is CI-ready *as-is*: every test uses a tmp/`:memory:` DB + the autouse `XDG_STATE_HOME` redirect + `tmp_git_repo` (see `tests/conftest.py`); nothing depends on an absolute path, `$HOME`, or interactive input. A future CI runner runs the documented commands unchanged. **No CI config file added** (v1 = no CI; AC3 is a property of the suite). Documented in a README "CI-readiness" note.
  - [x] (Optional CI workflow) operator did not request it — left deferred per the v1 requirement.
- [x] **Task 4 — Logging audit (AC: 4)**
  - [x] Confirmed `cli._configure_logging()` reads `DEV_HELPER_LOG` (default `INFO`), logs to **stderr** (`basicConfig` default), and every module uses `logging.getLogger(__name__)` (9 modules).
  - [x] **Audit result:** the `repo_unavailable:` and total-failure paths already log at `WARNING` with `error.code` only (cache.py, core/worktrees.py); `orphan_link:` is surfaced in `/state` + the dashboard (diagnosable). **One real gap:** a *typed* `DevHelperError` tool failure logged nothing server-side (only the unexpected/`Internal` path logged). Added a minimal `logger.info("<tool> failed: %s", exc.code)` on each of the 5 handler typed-error branches — **`error.code` only, never the description/annotation body** (NFR-7). No orphan-link log added: it is already diagnosable via `/state`/dashboard and per-tick logging would be noisy (kept minimal).
  - [x] **Test (fast):** added `tests/test_logging.py` — (a) default level `INFO`, `DEV_HELPER_LOG=DEBUG` raises the effective level, unknown value falls back to `INFO`, stream is stderr (not stdout); (b) the `update_task`→`TaskNotFound` path (git-free, tmp `Store`) logs the `error.code` at INFO and **does not** emit the user description body (asserted absent via `caplog`).
- [x] **Task 5 — Gate green + close out Epic 3 / v1** (AC: all)
  - [x] Full manual gate green over the complete suite: ruff (check + format --check, 56 files) + `pytest -m "not slow"` (314) + `node --test tests/js/` (53) + `pytest -m slow` (10). `tests/test_adapter_seam.py` green. No schema/git/tool-surface change (5 tools unchanged).
  - [x] This is the last v1 story. Scope held to confirmation + the minimal logging diagnostic line + README. `epic-3` can now move toward `done` + retrospective.

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
3.3 is a **confirmation + packaging-docs + logging-audit** story, NOT a feature build. It proves the tool installs as a daily console command, that the quality gate (established in 1.1) still passes over the complete Epics 1–3 suite, that the suite is CI-ready, and that logging is diagnostic-but-not-leaky.

- **BUILD (minimal):** a README/docs install + quality-gate-reality section; a small logging audit + `tests/test_logging.py`; any *minimal* missing diagnostic log line. Confirm pyproject entry point / build backend / pins.
- **DO NOT BUILD (out of scope — hard fence):**
  - **The pre-commit gate, the entry point, the `uv_build`/`src` layout, `_configure_logging`** — all **established in Story 1.1**. 3.3 confirms; it does not re-introduce them.
  - **Do NOT re-arm `pytest` in `.githooks/pre-commit`** without an explicit operator decision (Decision A) — the hook is ruff-only by operator decision (2026-06-25); tests are manual.
  - **No new feature, tool, endpoint, schema, or lock/CLI behavior** — 3.1/3.2 own lifecycle; Epics 1–2 own the rest. No CI workflow file (v1 = no CI) unless the operator asks.
  - **No PyPI publish** — `uv tool install .` / a local wheel is the v1 install story; publishing is out of scope.
- [Source: epics.md:532-555 (this story); epics.md:14 (amendment 2026-06-22c — gate established in 1.1); epics.md:71 (AR-12).]

### 🚨 ✅ Decision A — the gate is ruff-in-hook + MANUAL tests; AC2 means "the full gate stays green at full scope", not "re-arm pytest in the hook" (OPERATOR CONFIRMATION REQUIRED)
The architecture/epics text for AR-12/Story 3.3 says the pre-commit hook runs `ruff` + `pytest`. The **live reality** (operator decision 2026-06-25, recorded in 2.4c) is: `.githooks/pre-commit` runs **only `ruff`**; `pytest -m "not slow"` + `node --test tests/js/` are run **manually**. "This file wins over architecture pseudo-code." So AC2 is best read as: *the complete gate — ruff-in-hook + the manual test commands — passes green over the full Epics 1–3 suite and is CI-ready.* **The dev agent must get explicit operator sign-off before touching `.githooks/pre-commit`.** Recommended resolution: keep the hook ruff-only, demonstrate the full manual gate green over the whole suite, and document the split. If the operator instead wants pytest back in the hook for the v1 close-out, that is their call to make — not a silent dev change. Record the resolution in Completion Notes + Change Log.

### Critical gotchas (carry into implementation)
- **Don't restore pytest to the hook silently** (Decision A) — the single biggest trap in this story. The hook is intentionally ruff-only.
- **The entry point + build backend already exist** (pyproject from Story 1.1) — verify, don't recreate. `requires-python >= 3.14` (the PEP 758 `except` syntax used in the codebase requires it — see 2.4c's dismissed "syntax" finding; a `<3.14` install will not even import some modules, which is *by design* and gated by `requires-python`).
- **Logging: user content is not a secret but IS sensitive at INFO.** There are no passwords/tokens, but task **descriptions/annotations are user prose** — keep them out of `INFO` (log `task_id`/`status`/`error.code`, not the body). This is the concrete meaning of "never log full annotation contents at INFO" (NFR-7).
- **stderr, not stdout.** `logging.basicConfig` defaults to stderr — keep it; the dashboard URL print is the one deliberate **stdout** line (Story 1.1) and must stay stdout (operators pipe/redirect logs separately).
- **CI-readiness is a property, not a file.** Don't add `.github/workflows` to "satisfy" AC3 — assert the suite has no local-path/HOME/interactive deps (it doesn't, thanks to the autouse fixtures) and document that `pytest` runs unchanged under CI.
- **Slow tests stay opt-in.** The real-port smoke + 3.1/3.2 lock/lifecycle slow tests are `@pytest.mark.slow`; the fast gate is `-m "not slow"`. The full-suite confirmation runs both at least once.
- **No `pytest-asyncio`; `tmp_git_repo` for any git; project repo read-only** (autouse guards) — unchanged.

### Binding invariants (architecture.md §Invariants; project-context.md)
- **AR-12 quality gate** established in 1.1 (amendment 2026-06-22c), browser-free dashboard tests (HTML-output + static lint + WCAG math + `node --test diff()`), `tests/` mirrors `src/`. 3.3 confirms scale + CI-readiness. [epics.md:71, 14; architecture.md#L535-538]
- **NFR-7 Observability:** stdlib logging to stderr, level via `DEV_HELPER_LOG` (default INFO), `getLogger(__name__)` per module, never secrets / full annotation contents at INFO. [epics.md:54; architecture.md#L534, #L647-649]
- **NFR-6 Simplicity/Footprint:** single easy global install, minimal deps (`mcp` + `aiosqlite`, Starlette/uvicorn transitive). `uv tool install`. [epics.md:53; architecture.md#L532-533]
- **Run/dist:** console entry `dev-helper-mcp = dev_helper_mcp.cli:main`; also `python -m dev_helper_mcp`; no `--repo`. [project-context.md#Run/dist]

### Previous-story intelligence that applies directly
- **2.4c (operator decision 2026-06-25):** the pre-commit hook is **ruff-only**; tests are manual. This is THE fact that reframes AC2 (Decision A). [2-4c Dev Notes "Critical gotchas"; project-context.md#Code-quality gate]
- **Story 1.1:** the gate, the entry point, `uv_build`, `requires-python>=3.14`, `_configure_logging` (`DEV_HELPER_LOG`, stderr) all already exist — confirm, don't re-add. The dashboard-URL print is stdout. [1-1 File List; cli.py:16-21]
- **Logging convention (1.1 + all stories):** `getLogger(__name__)` per module, configured once in `cli.main`. The 1.6 startup version-check raises `Internal` rather than logging separately — a model for "fail clearly without leaking". [project-context.md#Naming & structure]
- **Test discipline:** in-process ASGI + tmp/`:memory:` DB + autouse XDG isolation + `tmp_git_repo`; no `pytest-asyncio`; one+ slow real-port test. All CI-portable already. [project-context.md#Testing rules]

### Git / recent-work intelligence
- **Sequence:** 3.1 → 3.2 → **3.3** (the v1 closer). 3.3 should run last so "full v1 suite complete" (AC2) is literally true.
- **Commit cadence:** one commit per story post green-manual-gate. Expected files: UPDATE `README.md` (install + quality-gate-reality + CI-readiness notes), small logging additions if any, NEW `tests/test_logging.py`. Likely **no `src/` behavior change** beyond a possible minimal diagnostic log line. Do not touch `.githooks/pre-commit` without Decision A sign-off.

### Latest tech / version notes
- **Python 3.14** (`requires-python>=3.14`, ruff `target-version=py314`), `uv`, ruff line-length 100, `node v20.x` for `node --test`. **No new dependency.** `uv build` → wheel; `uv tool install` into an isolated venv. `mcp>=1.28,<2` (v2 is breaking — stay on 1.x), `aiosqlite>=0.22.1`; Starlette/uvicorn transitive (do not add as direct deps).
- **`uv tool install`** produces the `dev-helper-mcp` console script on `PATH`; `python -m dev_helper_mcp` via `__main__.py`.

### Project Structure Notes
- **UPDATE:** `README.md` (Install section: `uv tool install` / pipx, `dev-helper-mcp [--port N]`, `dev-helper-mcp stop`; a "Quality gate" note: hook = ruff, tests = manual `pytest -m "not slow"` + `node --test tests/js/`; a "CI-ready" note). Possibly tiny diagnostic `logger` additions in an existing module **only if** Task 4's audit finds a diagnosis gap.
- **NEW:** `tests/test_logging.py` (level-from-env + no-annotation-body-at-INFO).
- **UNCHANGED (do not edit):** `.githooks/pre-commit` (ruff-only by operator decision — **do not re-arm pytest without Decision A**), `pyproject.toml` (entry point/build/pins already correct — confirm only), `cli.py` (`_configure_logging` already correct), `lock.py`/`server.py` (3.1/3.2), all of `core/`/`git/`/`store.py`/`cache.py`/`projection.py`/the dashboard/`tools/`. **DB schema unchanged. Tool surface unchanged (5 tools).**
- **DEFERRED / out of scope:** any CI workflow file (v1 = no CI), PyPI publish, any feature/tool/endpoint/schema change. Epic 3 / v1 ends here.
- Test mirrors src: `tests/test_logging.py`.

### Testing standards
- **Fast:** `tests/test_logging.py` — `DEV_HELPER_LOG` sets the effective level (default INFO); a representative failed-tool / orphan path logs diagnostic fields (`task_id`/`status`/`error.code`) and does NOT emit a full description/annotation body at INFO (assert via `caplog`). Hand-built inputs where possible; `tmp_git_repo` for any git path.
- **Full-suite confirmation (AC2/AC3):** the complete manual gate green over Epics 1–3 — `ruff check` + `ruff format --check` + `pytest -m "not slow"` + `node --test tests/js/`, plus `pytest -m slow` at least once (real-port smoke + 3.1/3.2 slow tests). Demonstrates the gate scales + is CI-ready (runs unchanged under a future CI runner).
- **Install (AC1):** documented manual / opt-in `uv build` + `uv tool install` + `dev-helper-mcp --help` (heavy + environment-mutating → not a default-suite test).
- **Coverage to the 4 ACs:** (1) console entry installs + runs from anywhere (manual/opt-in + pyproject confirm); (2) full gate green at full scope — *via the real ruff-hook + manual-tests split, Decision A* (not by silently re-arming the hook); (3) suite CI-ready (no local-path/HOME/interactive deps); (4) stderr logging, `DEV_HELPER_LOG` level, diagnostic-but-not-leaky.
- Green under the **manual** gate. `tests/test_adapter_seam.py` green.

### References
- [Source: epics.md:532-555] — Story 3.3 user story + all 4 BDD ACs verbatim (`uv tool install` console entry runnable anywhere; gate established in 1.1 confirmed at full scope; suite CI-ready/runs unchanged; stdlib logging to stderr, `DEV_HELPER_LOG` default INFO, diagnostic, no secrets / no full annotation contents at INFO).
- [Source: epics.md:14 (amendment 2026-06-22c)] — the enforced pre-commit quality gate was **moved into Story 1.1** to guard Epics 1–2; Story 3.3 reframed to install + full-scope gate confirmation (NOT introduction).
- [Source: epics.md:71 (AR-12)] — gate components (`ruff check`, `ruff format --check`, `pytest`); browser-free dashboard tests (HTML-output, static lint, WCAG math, `node --test diff()`); `tests/` mirrors `src/`; in-process ASGI + one real-port smoke (slow-marked).
- [Source: epics.md:53-54 (NFR-6, NFR-7)] — minimal-dependency single install; local logging to stderr via `DEV_HELPER_LOG`, sufficient to diagnose a failed tool call / orphaned link.
- [Source: architecture.md#L532-533] — distribution: `uv tool install`, console entry, `uv_build` + `src/` layout.
- [Source: architecture.md#L534, #L647-649] — logging: stdlib to stderr, `DEV_HELPER_LOG` level, `getLogger(__name__)`, never secrets / full annotation contents at INFO.
- [Source: architecture.md#L535-538] — gate / CI-readiness: ruff + pytest, suite runs unchanged under a future CI runner (no CI in v1).
- [Source: project-context.md#Code-quality gate & workflow] — **the REAL gate state**: `.githooks/pre-commit` runs ONLY ruff (operator decision); `pytest -m "not slow"` + `node --test tests/js/` are manual; ruff scoped via `extend-exclude`; run/dist via `uv run dev-helper-mcp` (no `--repo`); "this file wins over architecture pseudo-code".
- [Source: 2-4c-freshness-degraded-and-empty-states.md (Dev Notes "Critical gotchas" + Change Log)] — pre-commit test enforcement intentionally removed 2026-06-25 (gate is manual) — the fact that reframes AC2.
- [Source: pyproject.toml] — `[project.scripts] dev-helper-mcp = "dev_helper_mcp.cli:main"`, `uv_build`, `requires-python>=3.14`, `mcp>=1.28,<2` + `aiosqlite>=0.22.1`, dev `ruff`/`pytest`/`httpx`, `slow` marker — all from Story 1.1; confirm, don't recreate.
- [Source: src/dev_helper_mcp/cli.py:16-21] — `_configure_logging()` (`DEV_HELPER_LOG`, default INFO, stderr) already present.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- Full manual gate (post-change, complete Epics 1–3 suite): `ruff check .` ✅ + `ruff format --check .` ✅ (56 files) + `pytest -m "not slow"` → **314 passed** + `node --test tests/js/` → **53 passed** + `pytest -m slow` → **10 passed** + `tests/test_adapter_seam.py` ✅.
- RED→GREEN for the logging gap: `tests/test_logging.py::test_failed_tool_logs_code_but_not_description_body` failed before the handler change (no server-side trace for a typed error), passed after.

### Completion Notes List

- **This was a confirmation + packaging-docs + logging-audit story (the v1 closer), not a feature build.** No schema, git, endpoint, or tool-surface change — the 5 tools are unchanged.
- **Decision A (operator sign-off obtained 2026-06-29): keep `.githooks/pre-commit` ruff-only.** AC2's literal "hook runs ruff + pytest" was reconciled against the live reality (operator decision 2026-06-25: hook = ruff; tests = manual). The dev agent did **not** re-arm pytest in the hook. AC2 is satisfied by demonstrating the full manual gate (ruff + `pytest -m "not slow"` + `node --test tests/js/` + slow once) green over the complete suite, plus a README "Quality gate (the real split)" note. Also surfaced the hook's `ruff format .` (mutates) vs `ruff format --check .` (checks) discrepancy; operator chose to leave it as-is for this story.
- **AC1 (install):** `pyproject.toml` confirmed unchanged (entry point `dev-helper-mcp = dev_helper_mcp.cli:main`, `uv_build`, `requires-python>=3.14`, `mcp>=1.28,<2` + `aiosqlite>=0.22.1`, `slow` marker). The `uv build` / `uv tool install` / `python -m dev_helper_mcp` path is documented in the README as a manual/opt-in step (not a default-suite test — heavy + environment-mutating).
- **AC3 (CI-readiness):** confirmed as a property of the suite (tmp/`:memory:` DB + autouse `XDG_STATE_HOME` redirect + `tmp_git_repo`; no absolute-path/`$HOME`/interactive deps). No `.github/workflows` added (v1 = no CI). Documented in the README.
- **AC4 (logging):** audit found the logging contract largely already satisfied (stderr, `DEV_HELPER_LOG` level, `getLogger(__name__)` per module; `repo_unavailable`/total-failure log at WARNING with code only; `orphan_link` visible via `/state`+dashboard). **One real gap closed:** a typed `DevHelperError` tool failure logged nothing server-side. Added a minimal `logger.info("<tool> failed: %s", exc.code)` to each of the 5 handler typed-error branches — **`error.code` only, never the description body**. No orphan-link log added (already diagnosable; per-tick logging would be noisy — kept minimal).
- **Scope held:** the only `src/` change is the 5-line diagnostic log addition in `tools/handlers.py`. Everything else is README + a new test.

### File List

- `src/dev_helper_mcp/tools/handlers.py` — MODIFIED: added a minimal `logger.info("<tool> failed: %s", exc.code)` diagnostic line on each of the 5 handlers' typed-`DevHelperError` branches (code only, no body — NFR-7).
- `README.md` — MODIFIED: replaced the stale 1.1-skeleton copy with the real v1 description; added "Install" (`uv tool install`/pipx, `--port`, `stop`, `python -m`), "Logging" (stderr, `DEV_HELPER_LOG`, code-not-body), "Quality gate (the real split)" (hook = ruff; tests = manual), and "CI-readiness" sections.
- `tests/test_logging.py` — NEW: level-from-env (default INFO / DEBUG raises / unknown→INFO) + stderr-not-stdout; failed-tool path logs `error.code` at INFO but never the description body (via `caplog`).

## Review Findings

_Code review 2026-06-29 (adversarial 3-layer: Blind Hunter + Edge Case Hunter + Acceptance Auditor). Outcome: **0 decision-needed, 0 patch, 1 defer, 6 dismissed.** All 4 ACs met; Decision A honored; no scope-creep._

- [x] [Review][Defer] `logger.exception` fallback branch not proven body-safe under NFR-7 [src/dev_helper_mcp/tools/handlers.py:67,84,107,126,138] — deferred, pre-existing (the `except Exception` branch is unchanged by 3.3; standard tracebacks don't print local values, so the practical leak risk is negligible). Recorded in `deferred-work.md`.

**Dismissed (6):**
- _(false positive, HIGH)_ "`cli.py:96 except OSError, ValueError:` is a syntax error breaking test collection" — **verified false:** valid PEP 758 syntax under the project interpreter (uv → Python 3.14.2; `requires-python>=3.14`); the full suite incl. all 5 `test_logging.py` tests passed this session. The reviewer's `ast.parse` ran under system Python 3.10. Also outside 3.3's changeset (a pre-existing 3.1/3.2 edit). This is the "2.4c dismissed syntax finding" the project-context warns about.
- "`logger.info` is the wrong altitude / log spam" — INFO is intended (AC4 wants default-level diagnosis); verified none of the 5 handlers are on the `/state` poll path (it reads `cache.current` directly).
- "redaction test only covers the `update_task` path" — spec asks for "a representative failed-tool path"; all 5 sites are structurally identical `exc.code`-only logging.
- "caplog could be silenced by root-logger mutation → vacuous test" — refuted by Edge Case Hunter: the `try/finally` restores root handlers/level and the `LogCaptureHandler` intact.
- "redundant `levelno >= INFO` filter" — harmless.
- "test doesn't assert `data is None`" — non-material.

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-29 | Code review (adversarial 3-layer). 0 decision-needed, 0 patch, 1 defer (pre-existing `logger.exception` NFR-7 hardening → `deferred-work.md`), 6 dismissed (incl. a HIGH false-positive PEP 758 "syntax error" — valid under Python 3.14, suite green). All 4 ACs met, Decision A honored, no scope-creep. Status → done. |
| 2026-06-29 | Story 3.3 implemented (→ review). **Decision A resolved (operator sign-off): hook stays ruff-only; tests manual** — AC2 satisfied by the full manual gate green over the complete Epics 1–3 suite (ruff + `pytest -m "not slow"` 314 + `node --test` 53 + `pytest -m slow` 10), NOT by re-arming pytest in the hook. Confirmed `pyproject.toml` install/build/pins unchanged (AC1) and CI-readiness as a suite property (AC3). Logging audit (AC4): closed one gap — typed-error tool failures now log `error.code` (never the description body) via a minimal `logger.info` per handler; added `tests/test_logging.py`. Rewrote the stale 1.1-skeleton README with Install / Logging / Quality-gate-split / CI-readiness sections. No schema/git/tool-surface change. v1 closer — `epic-3` can move toward done + retrospective. |
| 2026-06-26 | Story 3.3 drafted (ready-for-dev): daily-use install + quality-gate confirmation + logging audit (the v1 closer). Confirms `uv tool install` console entry (`dev-helper-mcp`, already wired in 1.1's pyproject) runs from anywhere; confirms the gate scales to the full Epics 1–3 suite; confirms CI-readiness (suite has no local-path/HOME/interactive deps — no CI file needed in v1); audits logging (stderr, `DEV_HELPER_LOG` default INFO, diagnostic but never full annotation bodies at INFO) + adds `tests/test_logging.py`. **CRITICAL reconciliation (Decision A):** the pre-commit hook is ruff-only (operator decision 2026-06-25); tests are manual — AC2 means "the full gate stays green at full scope", NOT "re-arm pytest in the hook"; **operator sign-off required before touching `.githooks/pre-commit`.** Mostly confirmation + README/docs + minimal logging; no feature/schema/tool change. Hard prerequisite: 3.1 + 3.2 (so the full suite exists). |
