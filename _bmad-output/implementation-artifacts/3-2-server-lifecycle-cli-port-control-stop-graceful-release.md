# Story 3.2: Server lifecycle CLI — port control, stop, graceful release

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want explicit control over the port and a clean way to stop the server,
so that I can run and release the single global instance without reaching for `kill -9` or `rm -rf` on the lockfile.

## Acceptance Criteria

1. **`--port N` strict override; default scan (AR-10).**
   **Given** `--port N`,
   **When** I start the server,
   **Then** it binds exactly N or fails with `PortUnavailable` (strict override, no fallback); without `--port` it scans 8765→8775 and binds the first free port.

2. **Bound port written + printed; dashboard reads it; no `--repo`.**
   **Given** the server has bound a port,
   **When** it starts up,
   **Then** the actual bound port is written to the lockfile and printed with the dashboard URL, and the dashboard reads the bound port from the lockfile (never a hardcoded constant); there is **no `--repo` flag** (the server is global).

3. **`stop` / `--release-lock`.**
   **Given** a running server,
   **When** I run `dev-helper-mcp stop` (or `--release-lock`),
   **Then** the running instance is signaled to shut down cleanly and the lockfile is released.

4. **Clean shutdown releases the lock.**
   **Given** a clean shutdown via signal or `atexit`,
   **When** the process exits,
   **Then** the lockfile is released; the unclean path is covered by the stale-lock tolerance of Story 3.1.

## Tasks / Subtasks

- [ ] **Task 1 — `cli.py`: argument surface (AC: 1, 2, 3)**
  - [ ] `dev-helper-mcp [--port N]` to run; `dev-helper-mcp stop` subcommand and a `--release-lock` flag; **NO `--repo` flag** (the server is global, learns repos from `create_task`)
  - [ ] Dispatch: run → `server.py`; `stop`/`--release-lock` → signal the running instance + release the lock
- [ ] **Task 2 — `server.py`: port binding policy (AC: 1, 2)**
  - [ ] With `--port N`: bind exactly N or raise `PortUnavailable` (**strict, no fallback**)
  - [ ] Without `--port`: scan `PORT_RANGE` (8765→8775 from `config.py`, established in 1.1) and bind the first free port
  - [ ] Write the **actual bound port** into the lockfile `{pid, port, start_ts}` (extend 3.1's lock write) and print it with the dashboard URL (`http://127.0.0.1:<port>/`)
- [ ] **Task 3 — dashboard reads the bound port from the lockfile (AC: 2)**
  - [ ] Wherever the dashboard/URL needs the port (e.g. the Origin allowlist construction, printed URL, any self-reference), source it from the lockfile/bound value — **never a hardcoded constant**. (Recall 1.1's port↔Origin coupling: the allowlist is built from the bound port.)
- [ ] **Task 4 — `stop` / signal handling + graceful release (AC: 3, 4)**
  - [ ] `stop`/`--release-lock`: read the lockfile pid, send a clean shutdown signal (e.g. SIGTERM) to the running instance; the instance shuts down uvicorn cleanly and releases the lock
  - [ ] In the server process: install a signal handler (SIGTERM/SIGINT) + `atexit` that releases the lock (from 3.1's `release()`) and cancels the background refresher (from 2.2) cleanly
- [ ] **Task 5 — tests (`test_cli.py`; under AR-12 gate)**
  - [ ] `--port N` strict: bound to N, or `PortUnavailable` when N is taken (no fallback); without `--port` → first-free in range (auto-fallback)
  - [ ] No `--repo` flag exists (assert the parser rejects/does not define it)
  - [ ] Bound port is written to the lockfile and is what the printed URL / dashboard uses (not a constant)
  - [ ] `stop`/`--release-lock` dispatch signals shutdown + releases the lock; clean-exit (signal/atexit) releases the lock

## Dev Notes

### Scope boundaries — read first
Adds the **lifecycle CLI + port policy + clean stop** on top of 3.1's lock protocol and 1.1's minimal `server.py`/`cli.py`. **OUT of scope:** the stale-lock detection/identity-guard/takeover (Story 3.1 — this story *uses* 3.1's `release()` and lock-write); `uv tool install` packaging + gate-at-full-scope confirmation + logging level (Story 3.3). The background refresher exists (2.2) — this story ensures it is cancelled cleanly on shutdown.

### Builds on Stories 1.1, 2.2, 3.1 (previous-story intelligence)
- From **1.1**: `server.py` already binds `127.0.0.1` + scans `PORT_RANGE`; `cli.py` has minimal arg parsing with **no `--repo`**. This story makes `--port` strict, adds `stop`/`--release-lock`, and the bound-port write/print.
- From **3.1**: `lock.py` provides atomic acquire + `release()` + the `{pid, port, start_ts}` shape; extend the lock write with the actual bound port. The port-bind authoritative mutex (3.1 AC4) means a strict `--port` collision still maps cleanly (`PortUnavailable` for the strict-override path; `InstanceConflict` for the our-own-instance path — keep the two distinct).
- From **2.2**: the background refresher task must be cancelled on the graceful-shutdown path.
- **Port↔Origin coupling (from 1.1):** the Origin allowlist is built from the bound port. When `--port`/scan resolves the port, that value feeds both the allowlist and the lockfile — keep one source of truth. [Source: 1.1 Dev Notes; architecture.md#Authentication & Security]

### Lifecycle policy (architecture.md § Infrastructure & Deployment)
- Default scan **8765→8775**, bind first free; `--port N` is a **strict override** (bind N or fail `PortUnavailable`). The actual bound port is written to the lockfile and printed — the dashboard reads the lockfile, never a hardcoded constant. There is **no `--repo` flag** (server is global). [Source: architecture.md#Infrastructure & Deployment; #Port]
- `dev-helper-mcp stop` / `--release-lock` provided so nobody reaches for `rm -rf` on the lockfile. Released on clean shutdown (atexit + signal handler). [Source: architecture.md#Single-instance + lockfile]

### Error taxonomy
`PortUnavailable` = explicit `--port` bind failed (strict). `InstanceConflict` = our own machine-global instance already running (3.1). Keep them distinct — a strict `--port` taken by an *unrelated* process is `PortUnavailable`/`InstanceConflict` per the port-bind mutex rule; do not leak raw `OSError`. [Source: architecture.md#Error taxonomy]

### Source tree components to touch
`cli.py` (`--port`, `stop`, `--release-lock`; no `--repo`), `server.py` (strict/scan bind, bound-port write + print, signal/atexit release, cancel refresher), `lock.py` (port write + release, from 3.1), `config.py` (port range/paths, existing); `test_cli.py`. [Source: architecture.md#Complete Project Directory Structure; #Requirements → Structure Mapping FR-13]

### Project Structure Notes
- `server.py`/`cli.py`/`lock.py` are adapter/lifecycle layer (stdlib + uvicorn + the SDK app); core seam unaffected.
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 3.2: Server lifecycle CLI — port control, stop, graceful release] — acceptance criteria
- [Source: epics.md#AR-10] port fallback / strict override / stop
- [Source: architecture.md#Infrastructure & Deployment] — port policy, no `--repo`, bound-port-from-lockfile
- [Source: architecture.md#Single-instance + lockfile] — stop/release, clean-shutdown release

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
