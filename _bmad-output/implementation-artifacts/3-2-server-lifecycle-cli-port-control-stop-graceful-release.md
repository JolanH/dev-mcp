# Story 3.2: Server lifecycle CLI — port control, stop, graceful release

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want explicit control over the port and a clean way to stop the server,
so that I can run and release the single global instance without reaching for `kill -9` or `rm -rf` on the lockfile.

## Acceptance Criteria

1. **Given** `--port N`,
   **When** I start the server,
   **Then** it binds exactly N or fails with `PortUnavailable` (strict override, no fallback); without `--port` it scans 8765→8775 and binds the first free port.

2. **Given** the server has bound a port,
   **When** it starts up,
   **Then** the actual bound port is written to the lockfile and printed with the dashboard URL, and the dashboard reads the bound port from the lockfile (never a hardcoded constant); there is **no `--repo` flag** (the server is global).

3. **Given** a running server,
   **When** I run `dev-helper-mcp stop` (or `--release-lock`),
   **Then** the running instance is signaled to shut down cleanly and the lockfile is released.

4. **Given** a clean shutdown via signal or `atexit`,
   **When** the process exits,
   **Then** the lockfile is released; the unclean path is covered by the stale-lock tolerance of Story 3.1.

## ⛔ HARD PREREQUISITE — read before anything else

**Story 3.2 cannot be implemented until Story 3.1 is implemented.** It is the lifecycle CLI *on top of* 3.1's lockfile primitives.

- **Story 3.1** ships `lock.py` (`acquire`/reclaim/`release`, the `{pid, port, start_ts, identity}` lockfile, `config.lockfile_path()`), the acquire-before-serve + atexit/signal release wiring in `server.py`, and the port-bind-authoritative mutex. **3.2 reuses all of it** — the `stop` command reads `lock.py`'s lockfile to find the PID; graceful release calls 3.1's `release()`.
- **Story 1.1** ships `cli.py` (`main()` with `--port` parsed via `argparse`, `_configure_logging`) and `server.run(port=None)` with the 8765→8775 scan and the dashboard-URL print. **Extend these — do not rewrite.**
- **Story 1.2** ships `errors.PortUnavailable` (already defined). **Reuse it; do not redefine.**
- If 3.1 is not yet implemented, implement it first, then return here.

## Tasks / Subtasks

- [ ] **Task 1 — Strict `--port N` (no fallback) vs default scan** (AC: 1)
  - [ ] **The behavior change:** today `server.run(port)` *falls back to scanning* when the requested `--port` is unavailable (it `logger.warning`s and calls `find_free_port()`). AC1 requires the **opposite for an explicit `--port`**: bind exactly N **or raise `PortUnavailable`** — **no fallback**. Without `--port`, keep the 8765→8775 first-free scan.
  - [ ] Thread a "strict" intent from `cli.main()` (port explicitly given) into `server.run` — e.g. `run(port: int | None)` where `port is not None` ⇒ strict-bind-exactly, `port is None` ⇒ scan. On a strict bind hitting `EADDRINUSE`, raise `errors.PortUnavailable` with a clear message (the port number + that it is in use); do **not** scan, do **not** silently pick another port.
  - [ ] Keep `BIND_HOST = "127.0.0.1"`; the strict bind still binds loopback only.
- [ ] **Task 2 — Bound port → lockfile + printed URL; no `--repo`** (AC: 2)
  - [ ] The **actual bound port** is recorded in the lockfile (3.1 writes `{pid, port, start_ts, identity}` — ensure the `port` value is the *real* bound port, not the requested one, which matters for the default-scan path where the chosen port differs from `DEFAULT_PORT`). If 3.1 acquires the lock before the bind, update the `port` field after the bind resolves, or acquire after the bind so the recorded port is authoritative — coordinate with 3.1's ordering.
  - [ ] The dashboard URL is printed on startup from the bound port (Story 1.1 already does `print(f"dev-helper-mcp listening — dashboard: http://{BIND_HOST}:{bound_port}/", flush=True)` — keep it; it must reflect the *bound* port).
  - [ ] **"Dashboard reads the bound port from the lockfile (never a hardcoded constant)":** verify the served dashboard never hardcodes `8765`. The 2.4b poller already fetches the **relative** `/state` (same-origin), so the browser uses whatever port served the page — there is no hardcoded port in the client. Confirm this (grep the rendered page / `poller.js` for `8765` / absolute `http://127.0.0.1:` URLs → must be absent on the client poll path) and assert it in a test. The lockfile `port` is consumed by the **`stop` command** (Task 3) and any external reader, not by the in-page poller.
  - [ ] **No `--repo` flag:** the `argparse` parser must not define `--repo`; an attempt to pass it errors as unknown (the server is global and learns repos from `create_task`). Assert this.
- [ ] **Task 3 — `stop` / `--release-lock` command** (AC: 3)
  - [ ] Extend `cli.main()`'s `argparse` to dispatch a **`stop`** subcommand AND/OR a **`--release-lock`** flag (both routes per the AC — `dev-helper-mcp stop` is the primary; `--release-lock` is the equivalent flag). Decide the surface in Decision A below; implement both unless Decision A narrows it.
  - [ ] **Behavior:** read the lockfile (`lock.py` reader) → extract `pid` (+ identity for safety) → send `SIGTERM` to that PID (`os.kill(pid, signal.SIGTERM)`) → wait briefly for the instance to exit and **release its own lock** (Task 4 / 3.1's signal handler). Then exit 0.
  - [ ] **Edge handling (clear, non-opaque):** no lockfile → print "no running instance" and exit 0 (or a documented non-zero — pick and state it). Lockfile PID dead / identity-mismatch → print "instance not running; clearing stale lock" and remove the stale lockfile (this is the user-facing complement to 3.1's auto-reclaim). `os.kill` raises `ProcessLookupError` → same stale path. Never `kill -9` and never blindly `rm` a *live* instance's lock without signaling first.
  - [ ] **Identity-safety:** before signaling, confirm the lockfile PID is the *same* instance (3.1's identity guard) so `stop` never SIGTERMs an unrelated reused PID. On a non-Linux degrade, PID-liveness only (consistent with 3.1).
- [ ] **Task 4 — Graceful release on clean shutdown** (AC: 4)
  - [ ] On `SIGTERM`/`SIGINT` and via `atexit`, the running server releases its lockfile (3.1 wired the handlers + `release()`; 3.2 ensures the `stop`-sent `SIGTERM` flows through them to a clean shutdown — uvicorn stops accepting connections, the app-lifespan `finally` cancels the refresher + closes the store, and the lock is released). Confirm `stop` → SIGTERM → clean exit → lockfile gone, end to end.
  - [ ] The **unclean** path (`kill -9`) is explicitly NOT made clean here — it is covered by Story 3.1's stale-lock reclaim on the next start. Do not add a watchdog.
- [ ] **Task 5 — Tests** (AC: 1, 2, 3, 4) — **NEW `tests/test_cli.py`** (+ small `server.py` test additions)
  - [ ] **Arg parsing (fast, unit):** `parse_args([])` → start, no port; `["--port","9999"]` → start strict on 9999; `["stop"]` → stop mode; `["--release-lock"]` → release mode; `["--repo","x"]` → `SystemExit`/error (no `--repo`). Drive `main`'s parser directly (refactor parsing into a `build_parser()`/`parse_args()` so it is unit-testable without starting a server).
  - [ ] **Strict `--port` (AC1):** strict bind on a free port succeeds and records it; strict bind on an **occupied** port → `PortUnavailable`, **no fallback** (assert it did NOT scan/rebind elsewhere). The occupied-port case needs a real bound socket → `@pytest.mark.slow`. The no-`--port` scan path is already covered by Story 1.1's `find_free_port` tests — add a focused assert that `port is None` ⇒ scan, `port=N` ⇒ no scan (mockable without a real bind by injecting the bind step).
  - [ ] **Bound port → lockfile + URL (AC2):** after start, the lockfile `port` equals the bound port; the printed line contains the bound port. **Client has no hardcoded port:** assert the rendered dashboard / `poller.js` contains no `8765` and no absolute `http://127.0.0.1:<port>` on the poll path (relative `/state` only).
  - [ ] **`stop` (AC3, slow/e2e):** start a real instance (real port, `@pytest.mark.slow`), run `stop` (in-process call to the stop routine reading the lockfile), assert the instance receives SIGTERM, exits cleanly, and the lockfile is gone. Plus fast unit cases: `stop` with no lockfile → "no running instance"; `stop` with a dead PID → clears the stale lock; `stop` never signals a mismatched-identity PID.
  - [ ] **Graceful release (AC4):** a SIGTERM/atexit path releases the lockfile (can reuse/extend the 3.1 release test); confirm `stop`→SIGTERM→lock-gone.
  - [ ] **Git-safety:** CLI/lifecycle tests are OS/process-level — **no git**; the autouse `_isolate_state_dir` redirects `XDG_STATE_HOME` (lockfile in tmp), `_guard_project_repo_untouched` still applies. Real-instance `stop`/strict-port tests must use a tmp state dir + a real ephemeral port, never touch the project repo, and be `@pytest.mark.slow`.
- [ ] **Task 6 — Gate green + seam confirmation** (AC: all)
  - [ ] `cli.py` stays adapter-layer (it imports `server`/`lock`, no core seam violation); `tests/test_adapter_seam.py` green. No store/projection/cache/dashboard/tool/schema change.
  - [ ] Run the **manual** gate yourself: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` (+ the slow strict-port/stop tests at least once), plus `node --test tests/js/` (unchanged). ⚠️ Pre-commit hook runs **only ruff** — pytest/node is manual (operator decision 2026-06-25).

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
3.2 is the **operator-facing lifecycle CLI** layered on 3.1's lockfile: strict port control, a `stop`/`--release-lock` command, and confirmed graceful release.

- **BUILD:** strict `--port N` (no-fallback) in `server.run` + `cli.main`; the bound-port-into-lockfile + URL-from-bound-port wiring (over 3.1's lock); the `stop`/`--release-lock` command (read lockfile → SIGTERM → clean exit → lock released, with stale/dead-PID handling); the graceful-release confirmation; `tests/test_cli.py`.
- **DO NOT BUILD (out of scope / earlier- or later-owned — hard fence):**
  - **The lockfile itself / acquire / reclaim / identity guard / release primitive** — **Story 3.1** owns `lock.py`. 3.2 *calls* it; it does not re-implement locking.
  - **`uv tool install` packaging, the quality-gate-at-full-scope confirmation, the logging audit** — **Story 3.3**.
  - **No `--repo` flag, ever** (the server is global). No attach-to-running-instance protocol (a clear refusal/`stop` is the contract). No `kill -9`, no watchdog, no startup reconciliation (v1 non-goals).
  - **No new error class** — `PortUnavailable` exists in `errors.py` (Story 1.2).
- [Source: epics.md:508-531 (this story); epics.md:480-507 (3.1 owns the lockfile); epics.md:532-555 (3.3 owns install/gate/logging).]

### ✅ Decision A — implement BOTH `stop` (subcommand) and `--release-lock` (flag)
The AC says "`dev-helper-mcp stop` (or `--release-lock`)". Implement the **`stop` subcommand** as the primary, ergonomic surface and **`--release-lock`** as an equivalent flag that runs the same routine (some operators reach for a flag, some for a verb; both are cheap once the routine exists). If the team wants exactly one, keep `stop` (it reads better and leaves room for future verbs) and document `--release-lock` as an alias — but do not drop the AC's `--release-lock` wording silently. Refactor `cli.main` so argument parsing is a testable `build_parser()`/dispatch, not inline in `main`.

### ✅ Decision B — the in-page dashboard already satisfies "no hardcoded port" (verify, don't add)
AC2 says "the dashboard reads the bound port from the lockfile (never a hardcoded constant)". The **2.4b poller fetches the relative `/state`** (same-origin), so the browser inherently uses the serving port — there is **no hardcoded port in the client** to fix. So the *literal* "reads from the lockfile" is satisfied for the in-page client by same-origin relative requests; the **lockfile `port` field exists for the `stop` command and external/CLI readers**. Action: (1) ensure `server.run` records the *bound* port in the lockfile and prints the URL from it (not from `DEFAULT_PORT`), and (2) add a test asserting the client has no hardcoded `8765`/absolute URL. Do **not** invent a client-side lockfile read (the browser cannot read the lockfile, and same-origin already solves it). Record this reasoning in Completion Notes so a reviewer doesn't "fix" a non-issue.

### Critical gotchas (carry into implementation)
- **Reverse the current `--port` fallback.** `server.run` today *scans on `--port` failure* (a `logger.warning` + `find_free_port`). AC1 forbids that for an explicit `--port`: bind-exactly-or-`PortUnavailable`. Only the **no-`--port`** path scans. This is the headline behavior change — do not leave the fallback in for the explicit case.
- **Record the BOUND port, not the requested one.** On the default scan, the chosen port often differs from `DEFAULT_PORT`; the lockfile `port` and the printed URL must both be the *actual* bound port (so `stop` finds the right instance and the operator's URL works).
- **`stop` must be identity-safe.** Read the lockfile, confirm the PID is the same instance (3.1's identity guard) BEFORE `SIGTERM` — never signal a reused/unrelated PID. Dead PID / mismatch → clear the stale lock + "not running", never signal.
- **`stop` SIGTERMs, never `kill -9`.** Clean release flows through 3.1's signal/atexit handlers. The unclean path stays covered by 3.1's reclaim — don't add escalation.
- **`InstanceConflict` vs `PortUnavailable` are distinct codes.** `InstanceConflict` = another *dev-helper-mcp* instance holds the lock/port (3.1); `PortUnavailable` = an explicit `--port N` is taken (3.2, strict). Don't collapse them — `EADDRINUSE` on the *default scan* exhausting 8765→8775 is also a failure, but an explicit `--port` taken is specifically `PortUnavailable`.
- **`state_dir()`/`lockfile_path()` read the env each call** — never cache; the autouse test fixture relies on it.
- **No `pytest-asyncio`; slow-mark real-port/real-instance tests** (consistent with 1.1 and 3.1).
- **Keep `127.0.0.1`-only** through the strict-bind path (the smoke test asserts it).

### Binding invariants (architecture.md §Invariants; project-context.md)
- **AR-10 lifecycle:** strict `--port` or 8765→8775 scan; bound port to lockfile + printed URL; `stop`/`--release-lock`; **no `--repo`**; the server is global, long-lived, learns repos from `create_task`. [architecture.md#L512-518, #L530-533; epics.md:69, 44]
- **Run/dist:** `uv run dev-helper-mcp` (no `--repo`); console entry `dev-helper-mcp = dev_helper_mcp.cli:main` (already in pyproject). [project-context.md#Run/dist]
- **Typed errors / no stack-trace leak:** `PortUnavailable` is a `DevHelperError`; a strict-port failure ends as a clear logged error + non-zero exit (process lifecycle, not a tool envelope). [project-context.md#Data, format & error contract]
- **Bind `127.0.0.1` only.** [project-context.md#Security]

### Previous-story intelligence that applies directly
- **Story 3.1 (prereq)** owns `lock.py` (acquire/reclaim/release, `{pid,port,start_ts,identity}`, `lockfile_path()`) and the atexit/signal release wiring. 3.2 calls `release()` and reads the lockfile for `stop`. The `stop`→SIGTERM→clean-exit path runs through the handlers 3.1 installs. [3.1 Tasks 1, 5]
- **Story 1.1** owns `server.run`/`find_free_port`/the URL print and `cli.main`/`_configure_logging`. 3.2 extends `run` (strict vs scan) and `main` (`stop`/`--release-lock`, testable parser). [1-1 File List; server.py:48-67, cli.py:24-35]
- **Story 1.6/2.2** established the app-lifespan `finally` (cancel refresher, close store) — the clean SIGTERM shutdown drains through it before the lock release. [server_factory.lifespan]
- **Test discipline:** no `pytest-asyncio`; in-process ASGI where a live HTTP path is needed; one+ `@pytest.mark.slow` real-port test; autouse state-dir isolation + project-repo guard; **refactor `cli` parsing into a unit-testable function** (new pattern this story introduces, so the arg matrix is fast-tested without spawning servers). [project-context.md#Testing rules]

### Git / recent-work intelligence
- **Sequence:** 3.1 → **3.2** → 3.3 (forward-only). After 3.2, the operator has full lifecycle control (start/strict-port/stop/release); 3.3 then packages it (`uv tool install`) and confirms the gate + logging at full scope.
- **Commit cadence:** one commit per story post green-manual-gate + adversarial review. Expected files: UPDATE `cli.py` (testable parser, `stop`/`--release-lock` dispatch), `server.py` (strict-`--port`/`PortUnavailable`, bound-port→lockfile); NEW `tests/test_cli.py`. No store/projection/cache/dashboard/tool/schema change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100, `target-version=py314`. `signal.SIGTERM`/`SIGINT`, `os.kill`, `atexit`, `argparse` subparsers — all stdlib. **No new dependency.** uvicorn 0.49.0 (via `mcp`) graceful-shutdown semantics unchanged.

### Project Structure Notes
- **UPDATE:** `src/dev_helper_mcp/cli.py` (`build_parser()`/testable dispatch; `stop` subcommand + `--release-lock`; pass strict-port intent to `server.run`); `src/dev_helper_mcp/server.py` (strict `--port` → `PortUnavailable` no-fallback; record bound port in lockfile; URL from bound port).
- **NEW:** `tests/test_cli.py` (arg-matrix + strict-port + stop + graceful-release; slow-marked real-port/real-instance cases).
- **UNCHANGED (do not edit):** `lock.py` (Story 3.1 owns it — call, don't change), `errors.py` (`PortUnavailable` present), `server_factory.py`, `middleware.py`, `store.py`, `cache.py`, `projection.py`, all `core/`/`git/`, the dashboard (`render.py`/`poller.js`/`routes.py` — confirm no hardcoded port, but no edit expected), `tools/`. **DB schema unchanged.** `tests/test_adapter_seam.py` unchanged.
- **DEFERRED / out of scope:** the lockfile primitive (3.1); install/gate/logging (3.3); attach-protocol, watchdog, reconciliation (v1 non-goals).
- Test mirrors src: `tests/test_cli.py`.

### Testing standards
- **Fast unit:** the arg-parse matrix (`start`/`--port N`/`stop`/`--release-lock`/reject `--repo`); strict-vs-scan selection (inject the bind step — `port=None`⇒scan, `port=N`⇒no-scan); `stop` with no-lockfile / dead-PID / identity-mismatch (clears stale, never signals); bound-port recorded in lockfile; client has no hardcoded port.
- **Slow (`@pytest.mark.slow`):** strict `--port` on an occupied real port → `PortUnavailable` (no fallback); a real-instance `stop` → SIGTERM → clean exit → lockfile gone. `127.0.0.1` bind preserved.
- **Coverage to the 4 ACs:** (1) strict bind-exactly-or-`PortUnavailable`, scan only when no `--port`; (2) bound port → lockfile + printed URL, no hardcoded client port, no `--repo`; (3) `stop`/`--release-lock` signals + releases, with clean stale/dead handling; (4) signal/atexit release on clean exit, unclean covered by 3.1.
- Green under the **manual** gate (`ruff` + `ruff format --check` + `pytest -m "not slow"`, plus the slow strict-port/stop tests at least once, plus `node --test tests/js/` unchanged). `tests/test_adapter_seam.py` green.

### References
- [Source: epics.md:508-531] — Story 3.2 user story + all 4 BDD ACs verbatim (strict `--port N` or `PortUnavailable`, no fallback; bound port → lockfile + printed URL, dashboard reads bound port not a hardcoded constant, no `--repo`; `stop`/`--release-lock` clean signal + release; signal/atexit release, unclean covered by 3.1).
- [Source: epics.md:69 (AR-10)] — port auto-fallback 8765→8775 vs strict `--port`; port-bind authoritative; `stop`/`--release-lock`; no `--repo`.
- [Source: epics.md:44 (FR-13)] — one global long-lived server bound to 127.0.0.1, prints dashboard URL, no `--repo`, learns repos from `create_task`.
- [Source: architecture.md#L512-518] — process/port: default scan 8765→8775, strict `--port N` → `PortUnavailable`, bound port persisted + read by the dashboard.
- [Source: architecture.md#L530-533] — `stop`/`--release-lock`, signal + atexit release, console entry `dev-helper-mcp = dev_helper_mcp.cli:main`.
- [Source: src/dev_helper_mcp/server.py:48-67] — current `run(port)` with the *fallback-on-`--port`-failure* behavior to REVERSE for the strict case, plus the dashboard-URL print to keep.
- [Source: src/dev_helper_mcp/cli.py:24-35] — current `main()`/`argparse` (`--port` only) to extend into a testable parser with `stop`/`--release-lock`; no `--repo`.
- [Source: src/dev_helper_mcp/errors.py] — `PortUnavailable` already defined; reuse.
- [Source: 3-1-machine-global-single-instance-protection-with-stale-lock-recovery.md] — the lockfile primitive (`acquire`/reclaim/`release`, `{pid,port,start_ts,identity}`, `lockfile_path()`, atexit/signal handlers) that 3.2 consumes for `stop` + graceful release.

## Dev Agent Record

### Agent Model Used

_TBD — set by dev-story._

### Debug Log References

### Completion Notes List

### File List

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-26 | Story 3.2 drafted (ready-for-dev): server lifecycle CLI over 3.1's lockfile — strict `--port N` (bind-exactly-or-`PortUnavailable`, **reversing** Story 1.1's scan-on-failure fallback) vs default 8765→8775 scan; bound port recorded in the lockfile + printed URL (dashboard uses same-origin relative `/state`, so no hardcoded client port — verify, don't add); `dev-helper-mcp stop` + `--release-lock` (read lockfile → identity-safe SIGTERM → clean exit → lock released; stale/dead-PID → clear + "not running"); graceful signal/atexit release (unclean path covered by 3.1). No `--repo`. Decisions: A implement both `stop` + `--release-lock`; B same-origin relative poll already satisfies "no hardcoded port" (lockfile `port` is for `stop`/external readers). Hard prerequisite: Story 3.1. Reuses `errors.PortUnavailable` + `lock.py`. Install/gate/logging deferred to 3.3. |
