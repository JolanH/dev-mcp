// Story 2.4c — node --test for the poller's PURE freshness + warnings logic
// (Decision A: freshness is client-side, time-relative, and unit-tested as a pure
// function — the browser-free strategy applied to the time logic, not the DOM).
//
// Dependency-free: node's built-in test runner + node:assert (no npm, no jsdom).
// Run as part of the MANUAL gate:  node --test tests/js/
// The poller imports as a CommonJS module (its browser-only DOM/bootstrap code is
// guarded behind `typeof window`/`typeof document`, so it never runs under node).
//
// Covered:
//   * UX-DR6 — `staleness(generatedAtIso, nowMs, thresholdMs)`: below / above / exactly
//     at the threshold (exclusive: "exceeds 2× interval"), the relative-age label, and
//     totality on an unparseable / future stamp. `nowMs` is INJECTED (never Date.now()
//     inside the pure fn) — deterministic, mirroring 2.1's injected-clock discipline.
//   * UX-DR8 — `warningsIndex(warnings)`: parses `repo_unavailable:<repo>` (transient)
//     and `orphan_link:<task_id>@<repo>:<branch>` (genuinely gone) into their two
//     DISTINCT buckets — the headline gotcha (don't mix a slow repo with a dead branch).

import test from "node:test";
import assert from "node:assert/strict";
import poller from "../../src/dev_helper_mcp/dashboard/static/poller.js";

const { staleness, warningsIndex } = poller;

// A fixed base instant so the tests never read a real clock.
const BASE = Date.parse("2026-06-26T11:00:00Z");
const THRESHOLD = 3000; // = DASHBOARD_POLL_INTERVAL_MS(1500) * DASHBOARD_STALE_FACTOR(2)

function stampAtAge(ageMs) {
  // generated_at is BASE; "now" is BASE + ageMs -> the snapshot is ageMs old.
  return { generatedAt: "2026-06-26T11:00:00Z", nowMs: BASE + ageMs };
}

// ── UX-DR6: the stale threshold (exclusive) + relative-age label ──

test("under the threshold is NOT stale", () => {
  const { generatedAt, nowMs } = stampAtAge(1000);
  const s = staleness(generatedAt, nowMs, THRESHOLD);
  assert.equal(s.stale, false);
  assert.equal(s.label, "updated 1s ago");
});

test("over the threshold IS stale, with the aged label (grey->amber treatment)", () => {
  const { generatedAt, nowMs } = stampAtAge(7000);
  const s = staleness(generatedAt, nowMs, THRESHOLD);
  assert.equal(s.stale, true);
  assert.equal(s.label, "updated 7s ago");
});

test("EXACTLY at the threshold is NOT stale ('exceeds' => strictly greater, exclusive)", () => {
  const { generatedAt, nowMs } = stampAtAge(THRESHOLD); // age === threshold
  assert.equal(staleness(generatedAt, nowMs, THRESHOLD).stale, false);
});

test("one millisecond past the threshold IS stale", () => {
  const { generatedAt, nowMs } = stampAtAge(THRESHOLD + 1);
  assert.equal(staleness(generatedAt, nowMs, THRESHOLD).stale, true);
});

test("totality: an unparseable stamp is not stale and yields an empty label", () => {
  const s = staleness("", BASE, THRESHOLD);
  assert.equal(s.stale, false);
  assert.equal(s.label, "");
  assert.deepEqual(staleness("not-a-date", BASE, THRESHOLD), { stale: false, label: "" });
});

test("a future stamp (clock skew) clamps age to 0 — never negative, never stale", () => {
  const s = staleness("2026-06-26T11:00:00Z", BASE - 5000, THRESHOLD);
  assert.equal(s.stale, false);
  assert.equal(s.label, "updated 0s ago");
});

// ── UX-DR8: warningsIndex distinguishes the THREE degrade signals ──

test("repo_unavailable parses into the unavailable bucket (NOT an orphan)", () => {
  const idx = warningsIndex(["repo_unavailable:/code/repoB"]);
  assert.equal(idx.unavailable["/code/repoB"], true);
  assert.deepEqual(idx.orphans, [], "a transient/slow repo is NOT a dead-branch orphan");
});

test("orphan_link parses into task_id @ repo : branch (NOT marked unavailable)", () => {
  const idx = warningsIndex(["orphan_link:old-spike@/code/repoA:agent/old-spike"]);
  assert.deepEqual(idx.unavailable, {}, "a dead branch does not blank a repo's lines");
  assert.equal(idx.orphans.length, 1);
  assert.deepEqual(idx.orphans[0], {
    task_id: "old-spike",
    repo: "/code/repoA",
    branch: "agent/old-spike",
  });
});

test("a mixed warnings list splits cleanly into the two distinct buckets", () => {
  const idx = warningsIndex([
    "repo_unavailable:/code/repoB",
    "orphan_link:old-spike@/code/repoA:agent/old-spike",
    "repo_unavailable:/code/repoC",
  ]);
  assert.deepEqual(Object.keys(idx.unavailable).sort(), ["/code/repoB", "/code/repoC"]);
  assert.equal(idx.orphans.length, 1);
  assert.equal(idx.orphans[0].branch, "agent/old-spike");
});

test("totality: empty / undefined warnings yield empty buckets, never throws", () => {
  assert.deepEqual(warningsIndex([]), { unavailable: {}, orphans: [] });
  assert.deepEqual(warningsIndex(undefined), { unavailable: {}, orphans: [] });
});

test("an unrecognized warning kind is ignored (forward-compatible)", () => {
  const idx = warningsIndex(["some_future_warning:whatever"]);
  assert.deepEqual(idx, { unavailable: {}, orphans: [] });
});
