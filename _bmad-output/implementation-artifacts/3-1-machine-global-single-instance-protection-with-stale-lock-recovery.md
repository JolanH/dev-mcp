# Story 3.1: Machine-global single-instance protection with stale-lock recovery

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want exactly one `dev-helper-mcp` server per machine, with safe recovery from a dead instance's lock,
so that I never hit an opaque port-in-use crash, nor a server permanently blocked by a stale lock after a hard kill.

## Acceptance Criteria

1. **Atomic lock create (AR-10).**
   **Given** no server running,
   **When** I start `dev-helper-mcp`,
   **Then** it atomically creates `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/server.lock` via `os.open(O_CREAT|O_EXCL)` with `{pid, port, start_ts}`, binds the port, and proceeds.

2. **Live instance refused, never opaque EADDRINUSE (AR-10).**
   **Given** an existing lockfile whose recorded PID is alive (identity-matched on Linux via `/proc/<pid>` start-time / `boot_id`),
   **When** a second start is attempted,
   **Then** it refuses with a clear `InstanceConflict` message (or attaches), never an opaque `EADDRINUSE`.

3. **Stale lock reclaimed (AR-10).**
   **Given** an existing lockfile whose PID is dead or fails the identity guard,
   **When** a new server starts,
   **Then** it reclaims the lock via **atomic-rename takeover** and proceeds.

4. **Port-bind is the authoritative mutex (AR-10).**
   **Given** the chosen port is already bound by another process,
   **When** the server binds,
   **Then** the port-bind is the authoritative mutex — `EADDRINUSE` ⇒ `InstanceConflict` regardless of lock state (so PID-reuse false positives are non-fatal).

5. **Non-Linux degrades gracefully (NFR-Portability).**
   **Given** a non-Linux platform,
   **When** the identity guard cannot run,
   **Then** it degrades to PID-liveness only with a startup warning, the port-bind mutex remaining authoritative.

## Tasks / Subtasks

- [ ] **Task 1 — `lock.py`: atomic create (AC: 1)**
  - [ ] `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` on `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/server.lock` (path from `config.py`); write `{pid, port, start_ts}` (JSON); create parent dirs. The `O_EXCL` create serializes concurrent starts (kills TOCTOU)
- [ ] **Task 2 — `lock.py`: stale detection + identity guard (AC: 2, 3, 5)**
  - [ ] On `EEXIST`: read the lockfile; **pid liveness** via `os.kill(pid, 0)`; **plus** an identity guard against pid reuse — Linux-first using `/proc/<pid>` start-time (and/or `boot_id`)
  - [ ] Alive + identity-matched → refuse with `InstanceConflict` (clear message; or attach, per design) — never an opaque error
  - [ ] Dead or identity-mismatch → **atomic-rename takeover** (write a new lockfile to a temp name, `os.rename` over the stale one) and proceed
  - [ ] Non-Linux: identity guard unavailable → degrade to PID-liveness only + emit a startup **warning** (the port-bind mutex below remains authoritative)
- [ ] **Task 3 — port-bind authoritative mutex (AC: 4)**
  - [ ] After lock acquisition, the actual port bind is the final arbiter: `EADDRINUSE` ⇒ `InstanceConflict` regardless of lock state (so a PID-reuse false positive that wrongly reclaimed the lock still cannot double-bind)
  - [ ] Coordinate with `server.py` bind (the bind happens there; `lock.py` provides the protocol + maps the error)
- [ ] **Task 4 — release on clean shutdown (AC: 1; full lifecycle in 3.2)**
  - [ ] Provide a `release()` that removes/clears the lockfile; wire to `atexit` + signal handler (the `stop`/`--release-lock` CLI is Story 3.2 — here ensure clean-exit release exists so the stale path is the exception, not the norm)
- [ ] **Task 5 — extend `config.py` (AC: 1)**
  - [ ] Ensure the `server.lock` path (already alongside `state.db` from 1.2) is resolved here; no magic paths in `lock.py`
- [ ] **Task 6 — tests (`test_lock.py`; under AR-12 gate; deterministic)**
  - [ ] `O_EXCL` create; concurrent-start serialization (two acquirers, exactly one wins)
  - [ ] Stale takeover: **dead PID reclaimed**; **live PID refused** (`InstanceConflict`)
  - [ ] PID-reuse: a recycled PID that fails the identity guard → treated as stale (reclaim), not a false "live" refusal
  - [ ] Port mutex: simulated `EADDRINUSE` ⇒ `InstanceConflict` regardless of lock state
  - [ ] Non-Linux degrade path: identity guard absent → PID-liveness only + warning (mock the platform)

## Dev Notes

### Scope boundaries — read first
This is Epic 3's **single-instance/lockfile protocol** — the highest-risk part of lifecycle (a machine-global stale lock blocks *every* repo's agents). **OUT of scope:** the lifecycle CLI (`--port` strict override, `stop`, `--release-lock` command, printing the URL, dashboard reading the bound port) — that's **Story 3.2**; `uv tool install` packaging + gate confirmation — **Story 3.3**. Story 1.1 already does basic port *scanning* for a free port; this story adds the lock protocol around it.

### Why this graduated to a required AC (epics.md § Epic 3 risk note)
Because the lock is now **machine-global**, a stale lock (server `kill -9`'d) blocks **every** repo's agents — so **PID-liveness stale-lock detection** (reclaim a dead PID, refuse a live one) is required, with its own deterministic test. Note this lockfile is the **process-singleton** guard; **per-repo mutation safety is AR-14** (the per-repo async mutex, already built in Story 1.2) — do not conflate them. [Source: epics.md#Epic 3 risk notes; architecture.md#Invariants invariant 12]

### Lock protocol (pinned — architecture.md § Single-instance + lockfile, AR-10)
- **Atomic create** via `os.open(O_CREAT|O_EXCL)` (serializes concurrent starts — kills TOCTOU).
- On `EEXIST`, **stale check**: pid liveness (`os.kill(pid,0)`) **plus** an identity guard against pid reuse; dead/unrelated → **atomic-rename takeover**.
- Identity guard is **Linux-first** (`/proc/<pid>` start-time / `boot_id`); non-Linux **degrades to pid-liveness only + a startup warning** — acceptable because the port-bind mutex is authoritative regardless.
- **The port bind is the authoritative mutex** — `EADDRINUSE` ⇒ `InstanceConflict` regardless of lock state (makes pid-reuse false positives non-fatal).
- Released on clean shutdown (atexit + signal handler); stale tolerance covers the unclean path. [Source: architecture.md#Single-instance + lockfile]

### Error taxonomy
Use `InstanceConflict` from `errors.py` (1.2) — reserved for **same-machine** already-running with a live, identity-matched pid. Never surface a raw `EADDRINUSE`/`OSError` to the user. [Source: architecture.md#Error taxonomy; epics.md#AR-8]

### Builds on Stories 1.1 + 1.2 (previous-story intelligence)
- From **1.1**: `server.py` already binds `127.0.0.1` and scans 8765→8775 for a free port; `config.py` holds `PORT_RANGE`. This story wraps lock acquisition around that bind and maps bind errors to `InstanceConflict`.
- From **1.2**: `errors.py` (`InstanceConflict`), `config.py` resolves the `server.lock` path alongside `state.db`. `lock.py` is **adapter layer** (lifecycle), so it may use stdlib `os`/`signal`; it imports no `mcp`/`starlette` beyond what lifecycle needs (it's pure stdlib + errors). Keep the core seam intact.

### Source tree components to touch
`lock.py` (new — the protocol), `server.py` (acquire lock around the bind; map `EADDRINUSE`→`InstanceConflict`), `config.py` (lock path already there from 1.2 — confirm); `test_lock.py`. [Source: architecture.md#Complete Project Directory Structure — lock.py; #Requirements → Structure Mapping FR-13]

### Project Structure Notes
- The lockfile is **machine-global** at the XDG state dir alongside `state.db` — never in-repo, never under `src/`. One instance **per machine**, not per repo. [Source: architecture.md#Single-instance + lockfile; #Runtime state]
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 3.1: Machine-global single-instance protection with stale-lock recovery] — acceptance criteria + the machine-global stale-lock risk note
- [Source: epics.md#AR-10] single-instance + port fallback; [Source: epics.md#AR-8] error taxonomy (`InstanceConflict`)
- [Source: architecture.md#Single-instance + lockfile] — full lock protocol
- [Source: architecture.md#Invariants] — invariant 12 (lockfile = process singleton; AR-14 mutex is the separate per-repo guard)

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
