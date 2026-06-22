# Story 1.4: `create_task` cross-repo rollback (error-safe)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a Claude Code agent,
I want a partially-failed `create_task` to leave every repo and the store exactly as before the call,
so that a failure never leaves orphaned worktrees, branches, or task records.

## Acceptance Criteria

1. **Mid-flight failure → compensate already-created repos (AR-13).**
   **Given** repos `[A,B,C]` where worktree creation in C fails (`NotAGitRepo` / `BranchExists` / `BaseRefNotFound`),
   **When** `create_task` runs,
   **Then** the worktrees already created in A and B are removed (`git worktree remove --force`) AND their `agent/<task>` branches deleted (`git branch -D`), **in reverse order**;
   **And** no `task` / `task_worktree` rows persist (the DB looks like the call never happened).

2. **Preflight failure → start nothing (AR-13).**
   **Given** a preflight failure (a repo invalid before any mutation),
   **When** `create_task` runs,
   **Then** nothing is created in any repo (the cheapest rollback — don't start).

3. **Compensation itself fails → `RollbackIncomplete`, nothing swallowed (AR-8, AR-13).**
   **Given** a compensating teardown that itself fails,
   **When** rollback runs,
   **Then** `RollbackIncomplete` is returned with `details` listing the repo paths left orphaned, **preserving the original cause** as the failure reason; nothing is swallowed.

4. **Clean rollback → retry succeeds (no residue).**
   **Given** a clean rollback,
   **When** `create_task` is retried with the same `task_name`,
   **Then** it succeeds (no residual slug / branch / directory collision).

5. **`RepoMutator` seam + deterministic partial-failure matrix.**
   **Given** the worktree-mutation logic sits behind a `RepoMutator` seam,
   **When** tests inject `FlakyMutator(fail_on_repo=i, fail_on_phase={add|remove})`,
   **Then** the partial-failure matrix (fail on repo `i` of `N` for `i∈{1,2,N}`, `N∈{1,2,3}`) is deterministic and asserts zero worktrees, zero branches, zero rows.

## Tasks / Subtasks

- [ ] **Task 1 — extract the `RepoMutator` seam (AC: 5)**
  - [ ] Define a `RepoMutator` protocol/interface in `core/` with `add(repo_path, branch, worktree_path, base_ref)` and `remove(repo_path, branch, worktree_path)` (compensation) methods
  - [ ] Real implementation wraps `run_git()` mutation-pool calls (the actual `git worktree add` / `git worktree remove --force` / `git branch -D` from 1.3's `core/worktrees.py`)
  - [ ] `core/tasks.create()` takes the mutator by injection (default = real), so tests can pass `FlakyMutator`
- [ ] **Task 2 — error-safe compensation in `core/tasks.create()` (AC: 1, 2, 3)**
  - [ ] Track the ordered list of successfully-provisioned repos
  - [ ] On any `add` failure after earlier successes: compensate each succeeded repo in **reverse order** (`remove --force` + `branch -D agent/<task>`); write NO rows; re-raise the **original** cause
  - [ ] Preflight failure path: raise before any mutation (no compensation needed) — confirm 1.3's preflight short-circuit holds
  - [ ] If a compensating `remove`/`branch -D` itself fails: collect the orphaned repo paths and raise `RollbackIncomplete{details:{orphaned_repos:[...]}}` preserving the original cause; never swallow
- [ ] **Task 3 — `FlakyMutator` test double (AC: 5)**
  - [ ] `FlakyMutator(fail_on_repo=i, fail_on_phase)` that fails the `i`-th `add` (or the compensating `remove`) deterministically, recording calls so tests assert exact teardown order/counts
- [ ] **Task 4 — tests (`test_tasks.py` rollback slice; under AR-12 gate)**
  - [ ] Partial-failure matrix: fail on repo `i` of `N` for `i∈{1,2,N}`, `N∈{1,2,3}` → assert zero worktrees, zero branches, zero rows on disk and in DB (deterministic via `FlakyMutator`)
  - [ ] `RollbackIncomplete`: inject a compensation failure → assert code, `details.orphaned_repos`, and that the original cause is preserved in the failure reason
  - [ ] Clean-rollback retry: after a rolled-back failure, `create_task` with the same name succeeds (no slug/branch/dir residue) — exercise against **real** git (tmp repos), not just the mock
  - [ ] Preflight no-op: an invalid repo in the set → nothing created anywhere

## Dev Notes

### Scope boundaries — read first
This story makes `create_task` **error-safe** — it is a refactor of 1.3's happy-path `create()` to add compensation + the `RepoMutator` seam. **OUT of scope:** any new tool, list/remove/update tools (1.5/1.6), cache/dashboard (Epic 2). **Crash-safety (SIGKILL mid-create) is an explicit v1 NON-GOAL** — residue is a no-DB-row orphan worktree surfaced later by derive-on-read (Epic 2) and recoverable on a same-name retry (`BranchExists`); **do not build a startup reconciliation sweep** (that would re-introduce the deliberately-designed-out reconciliation engine).

### Builds on Story 1.3 (previous-story intelligence)
1.3 implemented `create()` with preflight + rows-last ordering and was deliberately structured so the "provision worktrees" step is a discrete, succeeded-list-producing loop. This story:
- Inserts the `RepoMutator` seam at exactly that loop, and the reverse-order compensation when a later `add` fails.
- Reuses 1.3's preflight (don't-start) unchanged — AC 2 is mostly a confirmation that preflight still short-circuits.
- Keeps the rows-last single-transaction commit (1.3) — compensation runs only on the pre-commit path, so "no rows persist" falls out naturally.
- Reuses `run_git()` mutation pool (1.2), the per-repo mutex (1.2), `errors.py` (`RollbackIncomplete`, `NotAGitRepo` already defined in 1.2).

### Compensation contract (pinned — architecture.md § create_task atomicity, AR-13)
- **Teardown order = reverse of creation.** Each compensation result is captured.
- **Error-safe is REQUIRED**, not best-effort: a failed `add` after earlier successes MUST compensate.
- **`RollbackIncomplete`** when a compensating teardown itself fails — `details` names the orphaned repo paths, the original cause is preserved. **Silent partial state is forbidden.**
- The read/refresh path NEVER calls a destructive git op — compensation runs in the mutation orchestration only. [Source: architecture.md#API & Communication Patterns; #Invariants invariant 10]

### Binding invariants
- **Invariant 10** — destructive git ops (`worktree remove --force`, `branch -D`) never on the read/refresh path; here they run only as compensation inside the mutation critical section. **Invariant 12** — `create_task` all-or-nothing: preflight → provision → rows last → on failure compensate → on compensation failure `RollbackIncomplete`. [Source: architecture.md#Invariants]

### Source tree components to touch
`core/tasks.py` (compensation + mutator injection), `core/worktrees.py` (the real `RepoMutator` remove/branch-delete ops), a `RepoMutator` protocol (core layer), `tests/test_tasks.py` (rollback matrix + `FlakyMutator`). No SDK imports in any of these (core layer). [Source: architecture.md#Complete Project Directory Structure]

### Project Structure Notes
- The `FlakyMutator` lives in tests (or a test helper), not in `src/`.
- No structural variance from the architecture tree.

### References
- [Source: epics.md#Story 1.4: `create_task` cross-repo rollback (error-safe)] — acceptance criteria + the crash-safety non-goal note
- [Source: epics.md#AR-13] atomicity/compensation/RollbackIncomplete; [Source: epics.md#AR-8] error taxonomy
- [Source: architecture.md#API & Communication Patterns] — create_task atomicity & rollback (AR-13)
- [Source: architecture.md#Invariants] — invariants 10, 12

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed — comprehensive developer guide created.

### File List
