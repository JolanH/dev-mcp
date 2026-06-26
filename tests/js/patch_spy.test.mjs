// Story 2.4b — Task 6 / Decision D: the UX-DR5 "zero DOM mutations on an identical
// poll" predicate, realized DEPENDENCY-FREE (no jsdom, no MutationObserver).
//
// The poll loop's construction is `const patches = diff(prev, next); if (patches.length)
// patch(...)`. So an identical consecutive /state ⇒ empty diff ⇒ `patch` is never
// called ⇒ ZERO DOM writes BY CONSTRUCTION. This test exercises exactly that guard
// with a spy standing in for `patch`, proving the invariant without a DOM:
//   * identical snapshot  → spy NOT called (0 writes);
//   * changed snapshot    → spy called once (a real change does reach the DOM).
//
// The primary, machine-checkable assertion remains `diff(x, x) === []` in
// diff.test.mjs; this corroborates the no-DOM-write consequence (Decision D).

import test from "node:test";
import assert from "node:assert/strict";
import poller from "../../src/dev_helper_mcp/dashboard/static/poller.js";

const { diff } = poller;

function snap(tasks, generated_at = "2026-06-26T11:00:00Z") {
  return { generated_at, tasks, warnings: [] };
}
function task(task_id, status) {
  return {
    task_id,
    description: "d",
    status,
    created_at: "2026-06-26T10:00:00Z",
    updated_at: "2026-06-26T10:30:00Z",
    worktrees: [],
  };
}

// Mirror the poll loop's patch guard exactly (the one branch that touches the DOM).
function pollStep(prev, next, patchSpy) {
  const patches = diff(prev, next);
  if (patches.length) patchSpy(patches, next);
  return next; // prev := next for the following tick
}

test("identical consecutive poll ⇒ patch is NEVER invoked (0 DOM writes)", () => {
  const state = snap([task("a", "running"), task("b", "blocked")]);
  let calls = 0;
  const spy = () => {
    calls++;
  };
  // Three identical polls in a row.
  let prev = state;
  prev = pollStep(prev, state, spy);
  prev = pollStep(prev, state, spy);
  pollStep(prev, state, spy);
  assert.equal(calls, 0, "patch must not run when the snapshot is unchanged");
});

test("identical poll where only generated_at advances ⇒ still 0 patch calls", () => {
  const a = snap([task("a", "running")], "2026-06-26T11:00:00Z");
  const b = snap([task("a", "running")], "2026-06-26T11:00:30Z");
  let calls = 0;
  pollStep(a, b, () => {
    calls++;
  });
  assert.equal(calls, 0);
});

test("a genuine change ⇒ patch IS invoked exactly once (sanity: the spy can fire)", () => {
  const before = snap([task("a", "running")]);
  const after = snap([task("a", "blocked")]);
  let calls = 0;
  let received = null;
  pollStep(before, after, (patches) => {
    calls++;
    received = patches;
  });
  assert.equal(calls, 1);
  assert.equal(received.length, 1);
  assert.equal(received[0].op, "update");
});
