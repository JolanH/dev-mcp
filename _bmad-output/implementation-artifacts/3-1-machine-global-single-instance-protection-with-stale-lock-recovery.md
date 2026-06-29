---
baseline_commit: afa513ea533f2b93a9df15d606b394d89c383222
---

# Story 3.1: Machine-global single-instance protection with stale-lock recovery

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the developer-operator,
I want exactly one `dev-helper-mcp` server per machine, with safe recovery from a dead instance's lock,
so that I never hit an opaque port-in-use crash, nor a server permanently blocked by a stale lock after a hard kill.

## Acceptance Criteria

1. **Given** no server running,
   **When** I start `dev-helper-mcp`,
   **Then** it atomically creates `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/server.lock` via `os.open(O_CREAT|O_EXCL)` with `{pid, port, start_ts}`, binds the port, and proceeds.

2. **Given** an existing lockfile whose recorded PID is alive (identity-matched on Linux via `/proc/<pid>` start-time / `boot_id`),
   **When** a second start is attempted,
   **Then** it refuses with a clear `InstanceConflict` message (or attaches), never an opaque `EADDRINUSE`.

3. **Given** an existing lockfile whose PID is dead or fails the identity guard,
   **When** a new server starts,
   **Then** it reclaims the lock via atomic-rename takeover and proceeds.

4. **Given** the chosen port is already bound by another process,
   **When** the server binds,
   **Then** the port-bind is the authoritative mutex — `EADDRINUSE` ⇒ `InstanceConflict` regardless of lock state (so PID-reuse false positives are non-fatal).

5. **Given** a non-Linux platform,
   **When** the identity guard cannot run,
   **Then** it degrades to PID-liveness only with a startup warning, the port-bind mutex remaining authoritative (NFR-Portability).

## ⛔ HARD PREREQUISITE — read before anything else

**Story 3.1 is the first Epic 3 story and hardens the minimal global server bootstrapped in Story 1.1 (and built out through Epics 1–2).** It depends only on already-completed work:

- **Story 1.1** shipped `server.py` (`_port_free`, `find_free_port(host, port_range)`, `run(port=None)`, the dashboard-URL print, `uvicorn.run`), `cli.py` (`main()` with `--port`, `_configure_logging`), and `config.py` port constants. **Do not re-create these — extend them.**
- **Story 1.2** shipped `errors.py` with **`InstanceConflict` and `PortUnavailable` already defined** (`code="InstanceConflict"` / `"PortUnavailable"`, with `.as_dict()`), and `config.state_dir()` → `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp` (reads the env each call — test isolation depends on this). **Reuse these; do not add new error classes or a second state-dir resolver.**
- There is **no `lock.py` and no `server.lock` today** — single-instance protection is entirely new in this story.
- **`PortUnavailable` (the strict-`--port` failure) and the `stop`/`--release-lock` CLI are Story 3.2**, not here. 3.1 builds the lockfile + acquire/reclaim/release primitives and wires acquire-at-startup + release-on-shutdown.

## Tasks / Subtasks

- [x] **Task 1 — `lock.py`: the lockfile primitive** (AC: 1)
  - [x] **NEW `src/dev_helper_mcp/lock.py`.** Add `config.LOCKFILE_NAME = "server.lock"` and a `lockfile_path()` helper in `config.py` returning `state_dir() / LOCKFILE_NAME` (mirror `default_db_path()` exactly — it reads `state_dir()` each call so XDG test-isolation works).
  - [x] Lockfile payload is JSON `{pid, port, start_ts, identity}` where `start_ts` is `util.now_iso()` (UTC ISO-8601 `Z`) and `identity` is the OS identity token (see Task 2). AC1 names `{pid, port, start_ts}`; `identity` is the additional field the Linux guard (AC2/Task 2) needs — keep the three named keys verbatim and add `identity` alongside (do not rename them).
  - [x] `acquire(port: int) -> LockHandle` (or module fns): create the parent dir (`state_dir()`, `mkdir(parents=True, exist_ok=True)`); attempt `os.open(path, O_CREAT|O_EXCL|O_WRONLY, 0o600)`, write the JSON, `os.close`. On success return a handle that knows it owns the lock. On `FileExistsError` (`EEXIST`) → go to the stale-check path (Task 3).
  - [x] Pure-ish + dependency-light: `lock.py` uses only `os`, `sys`, `json`, `errno`, `socket` (if it owns the bind — see Task 4 decision), `config`, `util`, `errors`. **It must not import `subprocess`, `aiosqlite`, or any git module.**
- [x] **Task 2 — OS identity token + liveness (AC: 2, 5)**
  - [x] **PID liveness:** `os.kill(pid, 0)` → `ProcessLookupError` (ESRCH) means dead → reclaim (Task 3). `PermissionError` (EPERM) means alive-but-not-ours → treat as alive. No exception → alive.
  - [x] **Linux identity guard:** when alive, confirm the PID is the *same* process, not a reused number. Compute an identity token from `/proc/<pid>/stat` field 22 (`starttime`, in clock ticks since boot) **and** `/proc/sys/kernel/random/boot_id`, e.g. `identity = f"{boot_id}:{starttime}"`. Compare to the lockfile's stored `identity`. **Match ⇒ same live instance ⇒ refuse (`InstanceConflict`).** **Mismatch ⇒ PID was reused ⇒ reclaim.** Read `/proc/<pid>/stat` by splitting on the LAST `)` first (the comm field can contain spaces/parens) — field 22 is the 20th whitespace token *after* that `)`.
  - [x] **Non-Linux degrade (AC5):** `/proc` absent (detect via `sys.platform != "linux"` or a missing `/proc/<pid>/stat`) → skip the identity guard, use **PID-liveness only**, and emit a one-line `logger.warning(...)` at startup (e.g. "identity guard unavailable on <platform>; using PID-liveness only — the port-bind mutex remains authoritative"). The token stored in the lockfile is then a degraded/empty marker; the guard treats any live PID as the same instance (refuse). Port-bind (Task 4) stays the real arbiter, so a PID-reuse false-positive here is non-fatal (the operator can `stop`/`--release-lock` — Story 3.2).
- [x] **Task 3 — Stale-lock reclaim via atomic-rename takeover (AC: 3)**
  - [x] On `EEXIST`: read+parse the existing lockfile. If it is unparseable/corrupt → treat as stale (reclaim). If the recorded PID is **dead** (ESRCH) or **fails the Linux identity guard** → reclaim.
  - [x] **Reclaim = atomic-rename takeover, never an in-place edit:** write the new `{pid, port, start_ts, identity}` to a temp file in the SAME dir (`server.lock.tmp.<pid>` or `tempfile.mkstemp(dir=state_dir())`, `0o600`), then `os.replace(tmp, lockfile_path())` (atomic on POSIX). Two racing reclaimers both rename their own tmp — last writer wins on the lockfile; the **port-bind mutex (Task 4) then rejects the loser**, so the rename race is benign by design.
  - [x] If the recorded PID is **alive and identity-matches** → do NOT reclaim → raise `InstanceConflict` with a clear message naming the running PID + port (AC2). ("or attaches" in the AC is optional — a clear refusal satisfies it; do not build an attach protocol.)
- [x] **Task 4 — Port-bind is the authoritative mutex (AC: 4)** — *the decision that makes lock races benign*
  - [x] `EADDRINUSE` at bind time ⇒ `InstanceConflict`, **regardless of lock state** (even if we just reclaimed the lock). This is the real single-instance guarantee; the lockfile is the *diagnostic/fast-path*, the bind is *authoritative*.
  - [x] **DECISION (recommended, ties to the deferred TOCTOU item):** bind the listening socket **once** in `lock`/`server` (`socket.socket` + `setsockopt(SO_REUSEADDR)` + `bind`) and hand the already-bound socket to uvicorn (`uvicorn.Server(Config(...))` with the passed socket / `fd`), instead of `find_free_port()` probing-then-`uvicorn.run` re-binding by number. This closes the Story 1.1 TOCTOU race (deferred-work.md: "TOCTOU port race", explicitly deferred to 3.1) AND gives a single place to map `EADDRINUSE → InstanceConflict`. If the team prefers minimal change, at least wrap the bind so `EADDRINUSE` raises `InstanceConflict` rather than crashing — but the bind-once-pass-to-uvicorn path is the intended fix. Flag the chosen approach in Completion Notes.
- [x] **Task 5 — Wire acquire-at-startup + release-on-shutdown into `server.py`** (AC: 1, 3, 4)
  - [x] In `server.run(...)`: choose the port (existing scan for the default path; strict `--port` is **Story 3.2** — keep current behavior here, just don't break it), **acquire the lock before binding/serving**, write the bound port into the lockfile (or acquire after bind so the real bound port is recorded — order to match Task 4's bind-once approach), then serve.
  - [x] **Release on clean shutdown:** register an `atexit` handler AND `signal` handlers for `SIGTERM`/`SIGINT` that call `lock.release()` (`os.remove` the lockfile **only if we own it** — guard with the handle so we never delete another instance's lock). The unclean path (`kill -9`) intentionally leaves a stale lock — that is exactly what Task 3 reclaims. (uvicorn installs its own SIGTERM/SIGINT handling; ensure our release still runs — via `atexit` as the backstop and/or chaining the signal handler. The app-owned lifespan `finally` from 1.6/2.2 already cancels the refresher + closes the store; the lock release is the new addition.)
  - [x] `logger = logging.getLogger(__name__)` per module; `logger.warning` on a stale reclaim and on the non-Linux degrade; `logger.info` on acquire with pid+port. Never log secrets (there are none).
- [x] **Task 6 — Tests** (AC: 1, 2, 3, 4, 5) — **NEW `tests/test_lock.py`**
  - [x] **O_EXCL atomicity (AC1):** `acquire(port)` on a clean state dir creates the lockfile with the right JSON; a second `acquire` while the first is held hits the `EEXIST` path. Assert the file contents (`pid`, `port`, `start_ts`, `identity`).
  - [x] **Stale dead-PID reclaim (AC3):** seed a lockfile with a guaranteed-dead PID + bogus identity → `acquire` reclaims (new pid/identity written) and succeeds. Get a guaranteed-dead PID deterministically: spawn a trivial child, `wait()` it, reuse its pid (now dead); or pick a pid and confirm `os.kill(pid,0)` raises ESRCH before asserting.
  - [x] **Live-PID identity-match refuse (AC2):** seed a lockfile with `os.getpid()` + the *current* process's real identity token → `acquire` raises `InstanceConflict` (alive + identity matches). Compute the token the same way the code does (read `/proc/self/stat` + `boot_id`).
  - [x] **PID-reuse → reclaim (AC2/AC3, Linux):** seed a lockfile with `os.getpid()` (alive) but a **mismatched** identity token (e.g. tweak the starttime) → `acquire` reclaims (the guard catches the reuse). Mark Linux-only (`@pytest.mark.skipif` on non-linux).
  - [x] **Port-bind authoritative (AC4):** bind a real socket on a chosen port in-test, then drive the bind path for that port → `InstanceConflict` (even after a lock reclaim). This needs a real port → `@pytest.mark.slow`.
  - [x] **Non-Linux degrade (AC5):** monkeypatch `sys.platform`/the `/proc` reader to be unavailable → `acquire` uses PID-liveness only and emits the warning (assert via `caplog`); a live PID still refuses, a dead PID still reclaims.
  - [x] **Release (AC1/AC3):** `release()` removes the lockfile only when owned; a `release()` that does not own the current lockfile is a no-op (never deletes another instance's lock).
  - [x] **Git-safety:** these are OS-level tests (`os.open`/`os.kill`/`/proc`), **no git** → no `tmp_git_repo` needed; the autouse `_isolate_state_dir` fixture already redirects `XDG_STATE_HOME` so the real `~/.local/state` is never touched. **No test may run git against the project repo** (the autouse `_guard_project_repo_untouched` guard still applies).
- [x] **Task 7 — Gate green + seam confirmation** (AC: all)
  - [x] `tests/test_adapter_seam.py` stays green. **`lock.py` is ADAPTER-layer per project-context.md (line 30 lists it with `server.py`/`cli.py`) — do NOT add it to `SEAM_MODULES`.** It needs no `mcp`/`starlette` and should stay OS-only, but it is not seam-restricted.
  - [x] Run the **manual** gate yourself: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not slow"` (and the slow port-bind lock test at least once: `uv run pytest -m slow -k lock`), plus `node --test tests/js/` (unchanged here). ⚠️ The pre-commit hook runs **only ruff** — the pytest/node run is manual (operator decision 2026-06-25).

## Review Findings

_Adversarial code review 2026-06-26 (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Acceptance Auditor: zero spec violations — all 5 ACs implemented + tested, Decisions A/B honored, scope fences respected. 15 findings dismissed as noise/handled/false-positive (incl. the `except OSError, ValueError:` "SyntaxError" — verified valid PEP 758 on Python 3.14.2)._

- [ ] [Review][Patch] Unsafe PID values reach `os.kill` (pid `0`/negative → signals process group → permanent false `InstanceConflict`; `pid:true` → probes PID 1) [src/dev_helper_mcp/lock.py:208 / _pid_alive:85-97] — guard for a positive non-bool int before the liveness probe; otherwise reclaim.
- [ ] [Review][Patch] Per-PID `/proc` read failure conflated with platform degrade — a PID dying between `os.kill(pid,0)` and the `/proc/<pid>/stat` read yields `identity=None`, so a live-then-dead PID is *refused* (and logs a misleading "identity guard unavailable on linux") instead of reclaimed [src/dev_helper_mcp/lock.py:153-164] — on Linux re-confirm liveness (dead → reclaim); reserve the degrade warning for genuine no-`/proc`/non-Linux.
- [ ] [Review][Patch] Socket FD leak if setup raises after bind — `create_app()`/`uvicorn.Config/Server` sit outside the `try/finally`, so an exception there leaks the bound `sock` and skips the `finally` release [src/dev_helper_mcp/server.py:129-141] — widen the guard to close `sock` on any post-bind failure.
- [ ] [Review][Patch] Docstring overstates "single startup warning" — the degrade warning fires per-resolution, not once per startup [src/dev_helper_mcp/lock.py:150-151] — reword the docstring.
- [x] [Review][Defer] Lockfile reclaim/release TOCTOU on the different-port diagnostic path [src/dev_helper_mcp/lock.py:178-214 / release:63-79] — deferred, benign by design (port-bind is the authoritative mutex; lockfile is the diagnostic guard). Worth a one-line code comment.
- [x] [Review][Defer] Signal handler runs file I/O (`open`+`json.load`+`os.remove`) inside `release()` [src/dev_helper_mcp/server.py:75-90] — deferred, low-risk (Python delivers signals at bytecode boundaries); the story mandated signal-handler release.
- [x] [Review][Defer] `state_dir()` existing as a regular file → raw `OSError` (not typed) at startup [src/dev_helper_mcp/lock.py:225] — deferred, extreme operator-error edge.
- [x] [Review][Defer] `os.write` short-write unchecked in `_reclaim`/`acquire` [src/dev_helper_mcp/lock.py:187,233] — deferred, self-healing (next acquire reclaims a corrupt file) and negligible for an ~80-byte payload.

### Review Findings — 2026-06-29 (re-review: Blind Hunter + Edge Case Hunter + Acceptance Auditor)

_All 5 ACs re-confirmed SATISFIED with tests (Acceptance Auditor). The prior review's 4 open `[Patch]` items are in fact ALREADY implemented in the code under review (verified directly): unsafe-PID guard present (lock.py:219-223); per-PID `/proc`-miss vs platform-degrade distinguished (lock.py:159-171); socket-FD-leak fix — `create_app`/uvicorn now inside the `try`/`finally` (server.py:131-145); the "single startup warning" docstring is reworded. **Recommend ticking those 4 boxes — they are stale, not open.** 12 findings dismissed as noise/false-positive, incl. the `except OSError, ValueError:` "SyntaxError" (re-verified valid PEP 758 — `py_compile` clean on the project's Python 3.14.2), the SO_REUSEADDR "weakened mutex" (Linux loopback still raises EADDRINUSE), and a `config`-module-shadow that does not exist (server.py imports `from .config import …`, binding no `config` name)._

- [x] [Review][Decision] **RESOLVED 2026-06-29 — ACCEPTED as a known deviation (operator decision).** Out-of-scope dashboard changes bundled into the 3.1 commit — commit `b236b90` ("3-1") also rewrites `src/dev_helper_mcp/dashboard/static/poller.js` (three unrelated Epic-2 fixes: `updateFreshness` NaN-threshold guard, `applyUnavailable` trailing-separator cleanup, `applyOrphans` signature keyed on `[task_id, repo, branch]`) plus a stray 2-4c entry appended to `deferred-work.md`. The story's File List omits `poller.js` and "Project Structure Notes" list the dashboard as UNCHANGED. No functional regression seen; the scope-fence/one-commit-per-story break is accepted as-is. (The poller.js internal edge cases are tracked under the `[Review][Defer]` item below for re-homing to the Epic-2 dashboard story.)
- [x] [Review][Patch] **FIXED 2026-06-29.** Valid-JSON-but-non-dict lockfile raises `AttributeError` instead of being treated as corrupt — `existing.get(...)`/`current.get(...)` run on `json.load` output; a non-dict (`42`, `[]`) passes the `except OSError, ValueError` (only `JSONDecodeError` is caught) and crashes `.get` — at startup in `_resolve_existing` (which is meant to reclaim corrupt files) and in the signal/atexit/finally shutdown path in `release()`. Added an `isinstance(..., dict)` guard → reclaim in `_resolve_existing`, no-op in `release()`. [src/dev_helper_mcp/lock.py:74, 213]
- [x] [Review][Patch] **FIXED 2026-06-29.** `release()` set `self._released = True` before confirming removal — a non-`FileNotFoundError` `os.remove` failure (e.g. EACCES/EBUSY) then propagated while the flag was already set, so the atexit/finally retry no-op'd and the lock leaked. Now `_released` is set only after a confirmed remove / confirmed not-owned / already-gone, leaving a transient failure retryable. [src/dev_helper_mcp/lock.py:67]
- [x] [Review][Defer] Lock file-I/O hardening against hostile filesystem states — `_reclaim`/`acquire` leak a `server.lock.tmp.<pid>` and surface a raw `OSError` (not a typed startup error) when `os.write`/`os.replace`/`os.open(O_EXCL)`/`mkdir` fail (ENOSPC, EACCES, EROFS, or `state_dir()` existing as a regular file) [src/dev_helper_mcp/lock.py:191-197,241,244-245] — deferred, low-risk operator/fs edge, self-limiting, same class as the already-deferred `state_dir()`-as-file and short-write items; worth a single hardening pass.
- [x] [Review][Defer] poller.js internal edge cases — `updateFreshness` guards only `isNaN`, not `threshold <= 0`; the trailing-space cleanup matches by `textContent === " "` (could strip a line's own separator); `diff`/orphan keys fall back to `"undefined"`/`""` for objects missing `task_id` [src/dev_helper_mcp/dashboard/static/poller.js] — deferred, belongs to the Epic 2 dashboard story that should own this code (not caused by the 3.1 lock work, and not reviewable against the 3.1 spec).

## Dev Notes

### Scope boundaries — read first (anti-scope-creep)
3.1 builds the **machine-global single-instance lockfile**: atomic create, PID-liveness + Linux identity guard, stale-lock atomic-rename reclaim, port-bind-as-authoritative-mutex, and clean-shutdown release — wired into the existing `server.run`.

- **BUILD:** `lock.py` (acquire/reclaim/release + identity token), `config.LOCKFILE_NAME`/`lockfile_path()`, the `server.py` acquire-before-serve + atexit/signal release, and `tests/test_lock.py`.
- **DO NOT BUILD (out of scope / later-owned — hard fence):**
  - **Strict `--port N` → `PortUnavailable` (no fallback), the `stop` command, and `--release-lock`** — those are **Story 3.2**. 3.1 keeps the current `--port`/scan behavior; it only adds lock acquire/release around it.
  - **No `uv tool install` packaging / quality-gate-confirmation / logging audit** — Story 3.3.
  - **No startup reconciliation / orphan-worktree sweep** — explicit v1 non-goal (crash-safety is documented as out of scope; a `kill -9` leaves a stale lock that Task 3 reclaims and a no-DB-row orphan worktree that derive-on-read surfaces — no reconciliation engine). [architecture.md AR-13 note; epics.md:261]
  - **No new error classes** — `InstanceConflict`/`PortUnavailable` already exist in `errors.py` (Story 1.2). Reuse them.
- [Source: epics.md:480-507 (this story); epics.md:508-531 (3.2 owns strict-port/stop); epics.md:532-555 (3.3 owns install/gate/logging).]

### ✅ Decision A — `lock.py` is ADAPTER-layer, NOT seam-restricted (project-context wins)
`project-context.md` line 30 explicitly lists `lock.py` among the adapter-layer modules (`server_factory.py`, `server.py`, `middleware.py`, `cli.py`, … `lock.py`) — i.e. it is *allowed* to import the SDK and is **not** in the core seam. So **do not add `lock.py` to `SEAM_MODULES`** in `tests/test_adapter_seam.py` (which guards `store.py`/`projection.py`/`cache.py` + `core/`/`git/`). In practice `lock.py` needs no `mcp`/`starlette`/`uvicorn` at all — keep it OS-only (`os`/`json`/`socket`/`sys`) for testability and v2-migration cleanliness — but this is a style choice, not a seam rule. *(Note: a prior code-scan suggested treating `lock.py` as core; project-context is the binding source and says adapter — follow it.)*

### ✅ Decision B — bind the socket once and hand it to uvicorn (recommended)
The port-bind is the *authoritative* single-instance mutex (AC4). The cleanest implementation binds the listening socket ourselves and passes it to `uvicorn.Server`, replacing Story 1.1's `find_free_port()` probe-close-then-`uvicorn.run`-rebind. This (1) makes `EADDRINUSE → InstanceConflict` a single mapped failure, and (2) **closes the deferred TOCTOU race** (deferred-work.md, "TOCTOU port race", explicitly deferred to Story 3.1). If the team chooses the minimal-change path instead, at minimum trap `EADDRINUSE` at bind and raise `InstanceConflict`; record the choice in Completion Notes. Either way, the bind — not the lockfile — is what guarantees one instance.

### The identity-guard mechanics (don't reinvent; get the parsing right)
- **Liveness:** `os.kill(pid, 0)` — ESRCH→dead, EPERM→alive(not-ours, still alive), no-error→alive.
- **Linux identity token:** `/proc/<pid>/stat` **field 22** = `starttime` (clock ticks since boot) + `/proc/sys/kernel/random/boot_id`. Token `f"{boot_id}:{starttime}"` stored in the lockfile at acquire; re-derived and compared on a later start. **Parsing trap:** `/proc/<pid>/stat`'s field 2 (`comm`) is wrapped in parens and may contain spaces/parens — split on the **last** `)` and count whitespace fields from there (field 22 overall = index 19 of the post-`)` split). A naive `line.split()[21]` is wrong for odd process names.
- **boot_id** ties the starttime to this boot, so a starttime that coincidentally matches across a reboot does not produce a false "same instance".
- **Non-Linux:** no `/proc` → PID-liveness only + a startup warning; the port-bind mutex carries the guarantee. macOS works (NFR-Portability); the guard "degrades", it does not error.

### Binding invariants (architecture.md §Invariants; project-context.md)
- **Machine-global, one instance (AR-10).** Lockfile at `${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp/server.lock`; this is the *process-singleton* guard. Per-repo mutation safety is a **separate** mechanism (AR-14 `RepoLockRegistry`, built in Epic 1) — do not conflate them. [architecture.md#L519-531; epics.md:69, 136]
- **Bind `127.0.0.1` only — never `0.0.0.0`** (the slow smoke test asserts this; keep it true through the bind-once refactor). [project-context.md#Security]
- **Runtime state in XDG, never under `src/`** — the lockfile sits beside `state.db` in `state_dir()`. [project-context.md#Persistence]
- **`now_iso()` for `start_ts`** — UTC ISO-8601 `Z`, never `datetime.now()`/epoch. [project-context.md; util.now_iso]
- **`{ok,data,error}` envelope / typed errors** — `InstanceConflict` is a `DevHelperError` with a stable `code`; surface it as a clear operator-facing startup message (this is a process-lifecycle path, not an MCP tool return, so it ends as a logged error + non-zero exit, not a tool envelope — but use the typed class, never a bare `RuntimeError`). [project-context.md#Data, format & error contract]

### Critical gotchas (carry into implementation)
- **`InstanceConflict` and `PortUnavailable` already exist** in `errors.py` (Story 1.2) — import, don't redefine.
- **`state_dir()` reads the env on every call** — never cache it at import; that is what makes the autouse `_isolate_state_dir` test fixture work. `lockfile_path()` must call `state_dir()` each time, exactly like `default_db_path()`.
- **Don't create asyncio objects at import time** — if any lock coordination touches asyncio (it likely should NOT; the lockfile is sync OS-level), construct it in the lifespan, never at module import (binds to the wrong loop). The lockfile is plain blocking `os` calls done once at startup, off the hot path — that's fine.
- **Release only what you own.** `release()` must guard against deleting a lockfile this process did not write (e.g. after a reclaim race the loser must not `os.remove` the winner's lock). Track ownership in the handle.
- **The unclean path is intentional.** `kill -9` leaves a stale lock — that's the whole point of Task 3's reclaim. Do not try to make `kill -9` clean (impossible) or build a reconciliation sweep (v1 non-goal).
- **Slow-mark real-port tests.** The fast gate runs `-m "not slow"`; any lock test that binds a real port (AC4) must be `@pytest.mark.slow`, matching the single real-port smoke test from Story 1.1.

### Previous-story intelligence that applies directly
- **Story 1.1** already binds `127.0.0.1`, scans 8765→8775 (`find_free_port`), prints the dashboard URL (`print(..., flush=True)` — keep it), and configures logging in `cli._configure_logging` (`DEV_HELPER_LOG`, default INFO, stderr). 3.1 wraps lock acquire/release around the existing `run()`. [1-1 File List; server.py:48-67]
- **Story 1.1 review deferred the TOCTOU port race to 3.1** — address it via Decision B. [deferred-work.md "Deferred from: code review of story-1-1"]
- **Story 1.2** gives `errors.InstanceConflict`/`PortUnavailable`, `config.state_dir()`/`default_db_path()`, and the app-lifespan teardown pattern (`finally` cancels the refresher + closes the store). The lock release is the new shutdown step alongside these. [1-2 File List; server_factory.lifespan]
- **Test discipline (1.1–2.4c):** no `pytest-asyncio` (`asyncio.run()` in sync tests); in-process `httpx.ASGITransport` for HTTP; exactly one (now-this-story-adds-more) `@pytest.mark.slow` real-port test; autouse `_isolate_state_dir` + `_guard_project_repo_untouched`. [project-context.md#Testing rules]

### Git / recent-work intelligence
- **Baseline:** Epic 2 closed (2.1–2.4c `done`). Epic 3 is `in-progress`, 3-1/3-2/3-3 `backlog`→this story sets 3-1 `ready-for-dev`. 3.1 is forward-only foundation for 3.2 (lifecycle CLI consumes lock acquire/release) and 3.3 (install/gate/logging).
- **Commit cadence:** one commit per story after a green manual gate + adversarial review. Expected files: NEW `lock.py` + `tests/test_lock.py`; UPDATE `config.py` (`LOCKFILE_NAME`/`lockfile_path()`), `server.py` (acquire/release wiring, possibly the bind-once refactor). No store/projection/cache/dashboard/tool change.

### Latest tech / version notes
- **Python 3.14**, `uv`, ruff line-length 100, `target-version=py314`; `from __future__ import annotations` per module convention. `os.replace` (atomic), `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)`, `os.kill(pid, 0)`, `/proc/<pid>/stat`, `/proc/sys/kernel/random/boot_id` are all stdlib/Linux-native — **no new dependency**.
- **No `mcp`/`starlette`/`uvicorn` API change**; if Decision B is taken, use `uvicorn.Server(uvicorn.Config(app, ...))` with a pre-bound socket (uvicorn 0.49.0, pinned via `mcp`).

### Project Structure Notes
- **NEW:** `src/dev_helper_mcp/lock.py` (adapter-layer, OS-only); `tests/test_lock.py`.
- **UPDATE:** `src/dev_helper_mcp/config.py` (`LOCKFILE_NAME = "server.lock"`, `lockfile_path()`); `src/dev_helper_mcp/server.py` (acquire-before-serve, bound-port-into-lockfile, atexit/signal release; optional bind-once-pass-to-uvicorn per Decision B).
- **UNCHANGED (do not edit):** `errors.py` (classes already present), `cli.py` (the `stop`/`--release-lock`/strict-port work is 3.2 — 3.1 leaves `cli.py` alone), `server_factory.py`, `middleware.py`, `store.py`, `cache.py`, `projection.py`, all `core/`/`git/`, the dashboard, `tools/`. **DB schema unchanged.** `tests/test_adapter_seam.py` unchanged (lock.py is adapter; not added to `SEAM_MODULES`).
- **DEFERRED / out of scope:** strict `--port`/`PortUnavailable`, `stop`, `--release-lock`, graceful-release CLI surface (3.2); install/gate/logging (3.3); startup reconciliation (v1 non-goal). The TOCTOU race (deferred from 1.1) is *resolved here* via Decision B.
- Test mirrors src: `tests/test_lock.py`.

### Testing standards
- **Unit (fast, `tests/test_lock.py`):** O_EXCL create + payload; stale dead-PID reclaim; live-PID identity-match refuse; PID-reuse reclaim (Linux-only skipif); non-Linux degrade + warning (monkeypatch platform/`/proc`); release-only-if-owned. Drive deterministically: real `os.getpid()`/identity for "alive"; a confirmed-ESRCH pid for "dead"; a tweaked identity for "reuse". No git.
- **Slow (`@pytest.mark.slow`):** the port-bind-authoritative case (AC4) needs a real bound socket; assert `EADDRINUSE → InstanceConflict` even after a lock reclaim. Keep `127.0.0.1` bind asserted.
- **Coverage to the 5 ACs:** (1) atomic create + JSON; (2) live+identity-match → refuse, never raw EADDRINUSE; (3) dead/identity-fail → atomic-rename reclaim; (4) port-bind authoritative regardless of lock; (5) non-Linux PID-liveness + warning.
- Green under the **manual** gate (`ruff check` + `ruff format --check` + `pytest -m "not slow"`, plus the slow lock test at least once, plus `node --test tests/js/` unchanged). `tests/test_adapter_seam.py` green with `lock.py` NOT added to the seam list.

### References
- [Source: epics.md:480-507] — Story 3.1 user story + all 5 BDD ACs verbatim (atomic O_CREAT|O_EXCL lockfile {pid,port,start_ts}; live+identity-match → InstanceConflict never EADDRINUSE; dead/identity-fail → atomic-rename reclaim; port-bind authoritative; non-Linux PID-liveness + warning).
- [Source: epics.md:69 (AR-10)] — global `${XDG_STATE_HOME}/dev-helper-mcp/server.lock {pid,port,start_ts}`; atomic O_CREAT|O_EXCL; stale-reclaim (pid + Linux identity guard, degraded elsewhere); port-bind authoritative mutex; **no `--repo`**.
- [Source: epics.md:136] — Epic 3 risk note: machine-global lock means a stale lock blocks *every* repo's agents → PID-liveness stale-lock detection is a required AC with its own deterministic test; this lockfile is the *process-singleton* guard, distinct from AR-14 per-repo mutex (Epic 1).
- [Source: architecture.md#L519-531] — single-instance + lockfile design: O_CREAT|O_EXCL, `{pid,port,start_ts}`, `os.kill(pid,0)` liveness, `/proc/<pid>/stat` starttime + `boot_id` identity guard, atomic-rename takeover, release via atexit/signal.
- [Source: architecture.md#L528-529] — port-bind authoritative: `EADDRINUSE ⇒ InstanceConflict` regardless of lock state (PID-reuse false positives non-fatal).
- [Source: architecture.md#L714-715] — `lock.py` module placement.
- [Source: project-context.md#L30] — adapter-layer module list **includes `lock.py`** (so not seam-restricted).
- [Source: project-context.md#L57, #L60] — runtime state in XDG (lockfile beside state.db); bind `127.0.0.1` only.
- [Source: src/dev_helper_mcp/errors.py] — `InstanceConflict`/`PortUnavailable` already defined; reuse.
- [Source: src/dev_helper_mcp/config.py:state_dir/default_db_path] — XDG resolver pattern to mirror for `lockfile_path()`; reads env each call (test isolation).
- [Source: src/dev_helper_mcp/server.py:18-67] — current `_port_free`/`find_free_port`/`run`/dashboard-URL print to extend (and the TOCTOU bind to fix per Decision B).
- [Source: deferred-work.md "code review of story-1-1"] — the TOCTOU port race, explicitly deferred to Story 3.1.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- Manual gate: `uv run ruff check .` ✓, `uv run ruff format --check .` ✓, `uv run pytest -m "not slow"` → 273 passed, `uv run pytest -m slow` → 7 passed (incl. 2 lock + the 1.1 uvicorn smoke), `node --test tests/js/` → 53 pass.
- `tests/test_adapter_seam.py` green; `lock.py` NOT added to `SEAM_MODULES` (it is adapter-layer per project-context.md:30).
- End-to-end wiring check (isolated `XDG_STATE_HOME`, real loopback port): lockfile created with `{pid,port,start_ts,identity}` during run, the server serves on the **once-bound** socket handed to uvicorn (Decision B), and the lockfile is removed on `SIGTERM` (clean release).
- Note: `lock.py`'s `except (OSError, ValueError)` was auto-reformatted by ruff to the parenthesis-free `except OSError, ValueError` form — valid under Python 3.14 (PEP 758), `target-version=py314`.

### Completion Notes List

- **Decisions taken (both as recommended by the story):**
  - **Decision A — `lock.py` is adapter-layer, NOT seam-restricted.** Kept it OS-only (`os`/`sys`/`json`/`errno`/`socket` + our `config`/`util`/`errors`); no `mcp`/`starlette`/`uvicorn`, no `subprocess`/DB/git import. Not added to `SEAM_MODULES`.
  - **Decision B — bind the socket once and hand it to uvicorn.** `lock.bind_socket()` binds a single loopback listening socket (`EADDRINUSE → InstanceConflict`), and `server.run()` passes it to `uvicorn.Server(...).run(sockets=[sock])` — no probe-close-then-rebind. This **resolves the TOCTOU port race deferred from Story 1.1** (marked resolved in `deferred-work.md`).
- **Lockfile primitive:** `acquire(port)` → `os.open(O_CREAT|O_EXCL|O_WRONLY, 0o600)` writing `{pid, port, start_ts(now_iso), identity}`; on `EEXIST`, `_resolve_existing` refuses (`InstanceConflict`) only for a live, identity-matched PID, else reclaims via atomic `os.replace(tmp, lockfile)`. `LockHandle.release()` removes the file only if `pid`+`identity` still match (release-only-if-owned), idempotent.
- **Identity guard:** PID liveness via `os.kill(pid,0)` (ESRCH=dead / EPERM=alive-not-ours / none=alive); Linux token `f"{boot_id}:{starttime}"` from `/proc/sys/kernel/random/boot_id` + field 22 of `/proc/<pid>/stat` (parsed by splitting on the LAST `)` then index 19). Match ⇒ refuse, mismatch ⇒ reclaim. Non-Linux / missing `/proc` ⇒ degrade to PID-liveness only + one-line `logger.warning`; degraded `identity` marker stored is `""`.
- **Shutdown release:** `atexit` (primary backstop) + SIGTERM/SIGINT handlers (cover the pre-serve window before uvicorn installs its own) + a `finally` after `server.run()` (uvicorn's graceful return). `kill -9` intentionally leaves a stale lock for the next start to reclaim — no reconciliation sweep (v1 non-goal).
- **Kept (not re-created):** `find_free_port`/`_port_free` remain in `server.py` for the slow uvicorn smoke test; they are no longer on the `run()` path. `errors.InstanceConflict`/`PortUnavailable` reused (no new error classes). `config.state_dir()` reused; `lockfile_path()` mirrors `default_db_path()` (reads env each call).
- **Out of scope (untouched, as fenced):** strict `--port`/`PortUnavailable`, `stop`, `--release-lock` (Story 3.2); install/gate/logging (3.3); `cli.py`, `errors.py`, store/projection/cache/dashboard/tools all unchanged; DB schema unchanged.
- **Tests:** `tests/test_lock.py` — 12 fast + 2 slow covering all 5 ACs (atomic create+payload+0o600, EEXIST refuse, dead-PID reclaim, corrupt-file reclaim, live+identity-match refuse, PID-reuse reclaim [Linux-only], non-Linux + missing-`/proc` degrade+warning, release-only-if-owned, port-bind-authoritative-after-reclaim, loopback bind). Deterministic dead PID via spawn-and-reap.

### File List

- `src/dev_helper_mcp/lock.py` (NEW)
- `tests/test_lock.py` (NEW)
- `src/dev_helper_mcp/config.py` (MODIFIED — `LOCKFILE_NAME`, `lockfile_path()`)
- `src/dev_helper_mcp/server.py` (MODIFIED — bind-once + lock acquire/release wiring, `_bind_scanning`, `_install_release`)
- `_bmad-output/implementation-artifacts/deferred-work.md` (MODIFIED — TOCTOU item marked resolved)

## Change Log

| Date | Change |
| --- | --- |
| 2026-06-26 | Story 3.1 implemented (status → review): NEW `lock.py` (acquire/reclaim/release + PID-liveness/Linux identity guard + `bind_socket`), `config.LOCKFILE_NAME`/`lockfile_path()`, `server.py` bind-once-then-hand-to-uvicorn (Decision B) + acquire-before-serve + atexit/signal/finally release, NEW `tests/test_lock.py` (14 tests, all 5 ACs). Decisions A (lock.py adapter-layer, not seam-restricted) + B (bind once → closes the TOCTOU race deferred from 1.1; marked resolved in deferred-work.md). Manual gate green (ruff + 273 fast + 7 slow + 53 node). |
| 2026-06-26 | Story 3.1 drafted (ready-for-dev): machine-global single-instance lockfile — atomic `O_CREAT|O_EXCL` create of `server.lock {pid,port,start_ts,identity}`, PID-liveness (`os.kill(pid,0)`) + Linux `/proc/<pid>/stat` starttime + `boot_id` identity guard, stale-lock atomic-rename reclaim, port-bind as the authoritative mutex (`EADDRINUSE → InstanceConflict`), non-Linux PID-liveness degrade + warning, acquire-before-serve + atexit/signal release wired into `server.py`. Decisions: A `lock.py` is adapter-layer (NOT seam-restricted — project-context.md:30); B bind the socket once and hand it to uvicorn (closes the TOCTOU race deferred from 1.1). Reuses existing `errors.InstanceConflict`/`PortUnavailable` + `config.state_dir()`. Strict-`--port`/`stop`/`--release-lock` deferred to 3.2; install/gate/logging to 3.3. |
