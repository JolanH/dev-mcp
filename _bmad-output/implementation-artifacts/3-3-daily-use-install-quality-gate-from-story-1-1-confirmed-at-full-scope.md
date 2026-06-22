# Story 3.3: Daily-use install (quality gate from Story 1.1 confirmed at full scope)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want to install the tool once as a console command, with the quality gate (established in Story 1.1) confirmed to cover the complete v1 suite,
so that it is a trustworthy daily tool and regressions are caught even without CI.

## Acceptance Criteria

1. **Console install (FR-13, NFR-Simplicity).**
   **Given** the project (`src/` layout, `uv_build` backend),
   **When** I run `uv tool install` (or pipx),
   **Then** a `dev-helper-mcp` console entry point is installed and runnable from any directory.

2. **Quality gate confirmed at full scope — established in 1.1, NOT introduced here (AR-12).**
   **Given** the enforced pre-commit hook **established in Story 1.1** (it was NOT deferred to this story),
   **When** the full v1 suite exists (Epics 1–3 stories complete),
   **Then** the hook still enforces `ruff check`, `ruff format --check`, and `pytest` (the in-process ASGI suite; the real-port uvicorn smoke test may be slow-marked/opt-in) on every commit — this story **confirms the gate scales to the complete suite**, it does not introduce it.

3. **CI-ready (no behavior change).**
   **Given** the test suite,
   **When** a CI runner is later introduced,
   **Then** the suite runs unchanged (it is CI-ready).

4. **Observability — logging contract (NFR-Observability).**
   **Given** the running server,
   **When** it logs,
   **Then** it writes stdlib `logging` to stderr at a level set by `DEV_HELPER_LOG` (default `INFO`), sufficient to diagnose a failed tool call or an orphaned link, never logging secrets or full annotation contents at `INFO`.

## Tasks / Subtasks

- [ ] **Task 1 — confirm/finalize packaging for `uv tool install` (AC: 1, 3)**
  - [ ] Verify `pyproject.toml` (from 1.1): `src/` layout, `uv_build` backend, the `dev-helper-mcp` console entry point (`dev_helper_mcp.cli:main`), runtime deps `mcp>=1.28,<2` + `aiosqlite` only; dev deps (`ruff`, `pytest`, `httpx`, `selectolax`) in the dev group so they are NOT installed for end users
  - [ ] `uv tool install .` (or pipx) installs `dev-helper-mcp` runnable from any directory; document install + run in `README.md`
  - [ ] Confirm the static dashboard assets (`dashboard/static/`) are packaged (included in the wheel) so the installed tool serves the board
- [ ] **Task 2 — confirm the AR-12 gate scales to the full suite (AC: 2, 3)**
  - [ ] Confirm the pre-commit hook from **Story 1.1** (`.githooks/pre-commit` + `core.hooksPath`) runs `ruff check`, `ruff format --check`, `pytest`, **and** the `node --test` for the poller `diff()` (added in 2.4b) across the complete Epics 1–3 suite
  - [ ] Decide + document the slow-test policy: the real-port uvicorn smoke test (1.1) and the fan-out perf/chaos test (2.2) may be `slow`-marked/opt-in so the per-commit run stays fast; the full suite (incl. slow) is what CI would run
  - [ ] **Do NOT re-introduce or re-scaffold the hook** — it exists since 1.1. This story only verifies coverage at full scope and documents the policy
- [ ] **Task 3 — logging/observability contract (AC: 4)**
  - [ ] Confirm every module uses `logging.getLogger(__name__)`; the root level is read from `DEV_HELPER_LOG` (default `INFO`), output to **stderr**
  - [ ] Audit log lines: enough to diagnose a failed tool call (error code + context) or an orphaned link; **never log secrets (there are none) or full annotation/description contents at `INFO`** (DEBUG may include more)
- [ ] **Task 4 — tests (under AR-12 gate)**
  - [ ] Install smoke (may be slow-marked): build the wheel / `uv tool install` into a temp env → `dev-helper-mcp --help` (or a no-op invocation) runs from an arbitrary directory
  - [ ] Logging: `DEV_HELPER_LOG` sets the level; logs go to stderr; assert no full description/annotation content is emitted at `INFO` (a representative tool-call/orphan log line)
  - [ ] CI-readiness: the suite has no hidden local-only dependency (paths/fixtures are temp/relative); document that `pytest` runs unchanged under CI

## Dev Notes

### Scope boundaries — read first
The **final story** — daily-use packaging + confirming (not building) the quality gate + the logging contract. **OUT of scope / explicit anti-pattern:** **re-introducing the pre-commit hook.** Per the 2026-06-22c amendment, the enforced gate was **moved into Story 1.1** so it guards Epics 1–2 from the start; this story **confirms it scales to the full v1 suite** and does NOT scaffold it. If you find yourself writing the hook from scratch, stop — it already exists. Also out of scope: any new feature, tool, or dashboard behavior.

### Why the gate is "confirmed" not "created" here (epics.md AMENDMENT 2026-06-22c)
The implementation-readiness M1 amendment moved the enforced pre-commit quality gate (AR-12) **from this story into Story 1.1** so it guards every subsequent story. Story 3.3 was reframed to **install + full-scope gate confirmation**. Treat AC 2 as a verification + documentation task, not an implementation task. [Source: epics.md AMENDMENT 2026-06-22c; #Story 1.1; #Story 3.3; #AR-12]

### Builds on the full project (previous-story intelligence)
- From **1.1**: `pyproject.toml` (entry point, `uv_build`, pins, dev group), the `.githooks/pre-commit` hook + `core.hooksPath` install, the in-process ASGI harness + real-port smoke test. Confirm these still hold at full scope.
- From **2.2**: the fan-out perf/chaos test (`test_perf_fanout.py`) — a candidate for `slow`-marking in the per-commit run.
- From **2.4b**: the `node --test` `diff()` test — confirm it is part of what the hook runs.
- From **3.1/3.2**: the lifecycle/lock/CLI — confirm the installed console tool runs the global server end-to-end.

### Distribution + observability (architecture.md § Infrastructure & Deployment)
- **Distribution:** `console_scripts` entry point `dev-helper-mcp`, installed via `uv tool install` (or pipx); `src/` layout, `uv_build` backend. [Source: architecture.md#Infrastructure & Deployment; #Distribution]
- **Logging:** stdlib `logging` to stderr; level via `DEV_HELPER_LOG`. **CI/CD:** none for v1 — the gate is the **enforced pre-commit hook** (`ruff check`, `ruff format --check`, `pytest`); the in-process suite runs per-commit, the real-port smoke test may be slow-marked/opt-in; the suite is CI-ready if contributors join. [Source: architecture.md#Logging/observability; #CI/CD]
- **Minimal dependency posture (NFR-Simplicity):** runtime = `mcp` (+ transitive Starlette/uvicorn) + `aiosqlite` only; everything else is dev-group. [Source: architecture.md#Dependency posture]

### Binding requirement (NFR-Observability, FR-13)
Logs must be sufficient to diagnose a failed tool call or an orphaned link, at a level set by `DEV_HELPER_LOG` (default `INFO`), never logging full annotation contents at `INFO`. [Source: epics.md#NFR-7; #Story 3.3 AC4]

### Source tree components to touch
`pyproject.toml` (confirm/finalize entry point + package data for `dashboard/static/`), `README.md` (install/run + hook-install docs), a light logging audit across modules (mostly verification), `.githooks/pre-commit` (verify scope, do not recreate); install-smoke + logging tests. [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping FR-13]

### Project Structure Notes
- Ensure `dashboard/static/` assets are included in the built wheel (package-data / `uv_build` include rules) — an installed tool with missing static assets would serve a broken board. [Source: architecture.md#File Organization Patterns — static assets shipped inside the package]
- No structural variance from the architecture tree. This story closes Epic 3 and the v1 scope.

### References
- [Source: epics.md#Story 3.3: Daily-use install (quality gate from Story 1.1 confirmed at full scope)] — acceptance criteria
- [Source: epics.md AMENDMENT 2026-06-22c] — gate moved to 1.1; 3.3 = install + full-scope confirmation
- [Source: epics.md#FR-13, #NFR-6, #NFR-7, #AR-1, #AR-12]
- [Source: architecture.md#Infrastructure & Deployment] — distribution, logging, CI/CD (enforced pre-commit)

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
