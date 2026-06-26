// Story 2.4b — node --test for the poller's PURE core (AR-12: "a node --test unit
// test for the poller diff() — the one small JS test added to the gate").
//
// Dependency-free: node's built-in test runner + node:assert, no npm install, no
// package.json (Decision C). Run as part of the MANUAL gate:
//     node --test tests/js/
// It is NOT wired into .githooks/pre-commit (pre-commit test enforcement was
// intentionally removed 2026-06-25; the hook runs only ruff).
//
// Imports the poller as a CommonJS module (default import → module.exports object),
// which works because the browser-only DOM/bootstrap code is guarded behind
// `typeof window`/`typeof document` checks and never runs under node.

import test from "node:test";
import assert from "node:assert/strict";
import poller from "../../src/dev_helper_mcp/dashboard/static/poller.js";

const { diff, contentHash, worktreeKey } = poller;

// ── snapshot builders ──
function wt(repo_path, branch, path, orphaned = false) {
  return {
    repo_path,
    branch,
    path,
    head: "abc",
    detached: false,
    locked: false,
    prunable: false,
    orphaned,
  };
}
function task(task_id, status, worktrees = [], extra = {}) {
  return {
    task_id,
    description: "d",
    status,
    created_at: "2026-06-26T10:00:00Z",
    updated_at: "2026-06-26T10:30:00Z",
    worktrees,
    ...extra,
  };
}
function snap(tasks, generated_at = "2026-06-26T11:00:00Z", warnings = []) {
  return { generated_at, tasks, warnings };
}

const SAMPLE = snap([
  task("api-refactor", "running", [wt("/code/repoA", "agent/api-refactor", "/code/repoA.wt/x")]),
  task("db-migration", "blocked", []),
  task("auth-docs", "done", []),
]);

// ── AC2 headline: diff(x, x) === [] ──

test("diff(x, x) returns an empty patch set (identical snapshots)", () => {
  assert.deepEqual(diff(SAMPLE, SAMPLE), []);
});

test("diff of two DISTINCT-but-value-equal snapshots is empty (deep value equality)", () => {
  // Rebuild an equal snapshot from scratch (different object identities).
  const other = snap([
    task("api-refactor", "running", [wt("/code/repoA", "agent/api-refactor", "/code/repoA.wt/x")]),
    task("db-migration", "blocked", []),
    task("auth-docs", "done", []),
  ]);
  assert.deepEqual(diff(SAMPLE, other), []);
});

test("a changing generated_at alone does NOT churn the board (excluded from the hash)", () => {
  const later = snap(SAMPLE.tasks, "2026-06-26T11:00:30Z");
  assert.deepEqual(diff(SAMPLE, later), []);
});

test("a warnings-only change does NOT emit a patch (2.4c degrade rides outside the diff)", () => {
  // Story 2.4c / UX-DR5+DR7 interplay: a poll that only adds/changes `warnings` (a repo
  // going unavailable, or a new orphan_link) produces an EMPTY diff — so `patch` is never
  // invoked and the Done/orphan `<details>` are never touched/collapsed. The degrade is
  // re-applied separately (applyUnavailable/applyOrphans), not through the task diff.
  const degraded = snap(SAMPLE.tasks, "2026-06-26T11:00:00Z", [
    "repo_unavailable:/code/repoA",
    "orphan_link:old-spike@/code/repoB:agent/old-spike",
  ]);
  assert.deepEqual(diff(SAMPLE, degraded), []);
});

test("a description-only change does NOT emit a patch (not rendered by 2.4a's markup)", () => {
  const next = snap([
    task("api-refactor", "running", [wt("/code/repoA", "agent/api-refactor", "/code/repoA.wt/x")], {
      description: "TOTALLY DIFFERENT",
    }),
    task("db-migration", "blocked", []),
    task("auth-docs", "done", []),
  ]);
  assert.deepEqual(diff(SAMPLE, next), []);
});

// ── per-op patch shapes ──

test("add: a task present only in next yields exactly one add op", () => {
  const next = snap([...SAMPLE.tasks, task("new-task", "review", [])]);
  const patches = diff(SAMPLE, next);
  assert.equal(patches.length, 1);
  assert.equal(patches[0].op, "add");
  assert.equal(patches[0].task_id, "new-task");
  assert.equal(patches[0].task.status, "review");
});

test("remove: a task present only in prev yields exactly one remove op", () => {
  const next = snap(SAMPLE.tasks.filter((t) => t.task_id !== "db-migration"));
  const patches = diff(SAMPLE, next);
  assert.deepEqual(patches, [{ op: "remove", task_id: "db-migration" }]);
});

test("status change: yields an update flagged status:true (drives a reparent)", () => {
  const next = snap([
    task("api-refactor", "review", [wt("/code/repoA", "agent/api-refactor", "/code/repoA.wt/x")]),
    task("db-migration", "blocked", []),
    task("auth-docs", "done", []),
  ]);
  const patches = diff(SAMPLE, next);
  assert.equal(patches.length, 1);
  assert.equal(patches[0].op, "update");
  assert.equal(patches[0].task_id, "api-refactor");
  assert.equal(patches[0].changed.status, true);
  assert.equal(patches[0].changed.worktrees, false);
  assert.equal(patches[0].task.status, "review");
});

test("worktree (field-only) change: update flagged worktrees:true, status:false", () => {
  const next = snap([
    task("api-refactor", "running", [
      wt("/code/repoA", "agent/api-refactor", "/code/repoA.wt/x"),
      wt("/code/repoB", "agent/api-refactor", "/code/repoB.wt/x"), // a new repo joined
    ]),
    task("db-migration", "blocked", []),
    task("auth-docs", "done", []),
  ]);
  const patches = diff(SAMPLE, next);
  assert.equal(patches.length, 1);
  assert.equal(patches[0].op, "update");
  assert.equal(patches[0].changed.status, false);
  assert.equal(patches[0].changed.worktrees, true);
});

test("a status change INTO done is an update (the patch layer reparents into the disclosure)", () => {
  const next = snap([
    task("api-refactor", "done", [wt("/code/repoA", "agent/api-refactor", "/code/repoA.wt/x")]),
    task("db-migration", "blocked", []),
    task("auth-docs", "done", []),
  ]);
  const patches = diff(SAMPLE, next);
  assert.equal(patches.length, 1);
  assert.equal(patches[0].op, "update");
  assert.equal(patches[0].changed.status, true);
  assert.equal(patches[0].task.status, "done");
});

// ── content hash: order-independent for worktrees ──

test("contentHash is order-independent for worktrees (sorted in the hash)", () => {
  const a = task("t", "running", [
    wt("/code/repoA", "agent/t", "/a"),
    wt("/code/repoB", "agent/t", "/b"),
  ]);
  const b = task("t", "running", [
    wt("/code/repoB", "agent/t", "/b"), // reversed order
    wt("/code/repoA", "agent/t", "/a"),
  ]);
  assert.equal(contentHash(a), contentHash(b));
  assert.equal(worktreeKey(a), worktreeKey(b));
  // ...and therefore a snapshot differing ONLY in worktree order produces no patch.
  assert.deepEqual(diff(snap([a]), snap([b])), []);
});

test("contentHash distinguishes a status change and a worktree change", () => {
  const base = task("t", "running", [wt("/code/repoA", "agent/t", "/a")]);
  const diffStatus = task("t", "blocked", [wt("/code/repoA", "agent/t", "/a")]);
  const diffWt = task("t", "running", [wt("/code/repoA", "agent/t", "/a2")]);
  assert.notEqual(contentHash(base), contentHash(diffStatus));
  assert.notEqual(contentHash(base), contentHash(diffWt));
});

// ── purity: diff does not mutate its inputs ──

test("diff is pure — it does not mutate either snapshot argument", () => {
  const prev = snap([task("a", "running", [])]);
  const next = snap([task("a", "blocked", [])]);
  const prevCopy = JSON.parse(JSON.stringify(prev));
  const nextCopy = JSON.parse(JSON.stringify(next));
  diff(prev, next);
  assert.deepEqual(prev, prevCopy);
  assert.deepEqual(next, nextCopy);
});

// ── totality: empty / missing fields never throw ──

test("diff is total on empty and absent task lists", () => {
  assert.deepEqual(diff(snap([]), snap([])), []);
  assert.deepEqual(diff({}, {}), []);
  assert.deepEqual(
    diff({ tasks: [] }, { tasks: [task("a", "running", [])] }),
    [{ op: "add", task_id: "a", task: task("a", "running", []) }],
  );
});
