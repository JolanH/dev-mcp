// Story 2.4c — DOM-layer coverage for the live degrade/empty/orphan/freshness re-apply.
//
// `staleness.test.mjs` covers the pure logic; this drives the REAL DOM functions
// (`updateFreshness` / `applyUnavailable` / `applyOrphans` / `applyEmptyStates`) against a
// TINY hand-rolled `document` stub — dependency-free, no jsdom (2.4b Decision D). It proves
// the predicates the pure tests can't:
//   * the freshness stamp text + `stale` class swap (UX-DR6/DR8) on one element;
//   * a `repo_unavailable:` repo's lines get marked while others stay normal — idempotently
//     (a re-apply with the same warnings makes NO further change, no duplicate marker);
//   * the orphan disclosure is created collapsed, REMOVED when orphans clear, and — the
//     UX-DR7 interplay — a USER-OPENED orphan `<details>` survives a re-apply (open kept,
//     same node object), never auto-collapsed/recreated;
//   * the empty-state copy is shown/hidden by the live counts (UX-DR9).

import test from "node:test";
import assert from "node:assert/strict";

// ── Minimal DOM stub (selector grammar: tag + .class* + [attr], descendant/child) ──
class El {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.nodeType = tag ? 1 : 3;
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = {};
    this._class = "";
    this._text = "";
  }
  get className() {
    return this._class;
  }
  set className(v) {
    this._class = v == null ? "" : String(v);
  }
  get children() {
    return this.childNodes.filter((n) => n.nodeType === 1);
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
  }
  getAttribute(k) {
    return k in this.attributes ? this.attributes[k] : null;
  }
  hasAttribute(k) {
    return k in this.attributes;
  }
  removeAttribute(k) {
    delete this.attributes[k];
  }
  _detach(node) {
    const i = this.childNodes.indexOf(node);
    if (i >= 0) this.childNodes.splice(i, 1);
    node.parentNode = null;
  }
  appendChild(node) {
    if (node.parentNode) node.parentNode._detach(node);
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }
  remove() {
    if (this.parentNode) this.parentNode._detach(this);
  }
  replaceChildren() {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
  }
  get textContent() {
    if (this.nodeType === 3) return this._text;
    return this.childNodes.map((n) => n.textContent).join("");
  }
  set textContent(v) {
    if (this.nodeType === 3) {
      this._text = String(v);
      return;
    }
    this.replaceChildren();
    const t = new El();
    t._text = String(v);
    t.parentNode = this;
    this.childNodes.push(t);
  }
  querySelector(sel) {
    return matchSelector(this, sel)[0] || null;
  }
  querySelectorAll(sel) {
    return matchSelector(this, sel);
  }
}
function mkText(v) {
  const t = new El();
  t._text = String(v);
  return t;
}
function parseCompound(token) {
  const spec = { tag: null, classes: [], attr: null, scope: token === ":scope" };
  if (spec.scope) return spec;
  const attr = token.match(/\[([^\]]+)\]/);
  if (attr) {
    spec.attr = attr[1];
    token = token.replace(/\[[^\]]+\]/, "");
  }
  const parts = token.split(".");
  if (parts[0]) spec.tag = parts[0].toUpperCase();
  for (let i = 1; i < parts.length; i++) if (parts[i]) spec.classes.push(parts[i]);
  return spec;
}
function matchesCompound(el, spec) {
  if (el.nodeType !== 1) return false;
  if (spec.scope) return true;
  if (spec.tag && el.tagName !== spec.tag) return false;
  const cls = el.className ? el.className.split(/\s+/) : [];
  for (const c of spec.classes) if (!cls.includes(c)) return false;
  if (spec.attr && !(spec.attr in el.attributes)) return false;
  return true;
}
function tokenize(sel) {
  const steps = [];
  let combinator = null;
  for (const tok of sel.trim().replace(/\s*>\s*/g, " > ").split(/\s+/)) {
    if (tok === ">") {
      combinator = ">";
      continue;
    }
    steps.push({ combinator, compound: parseCompound(tok) });
    combinator = " ";
  }
  return steps;
}
function descendants(el) {
  const out = [];
  for (const c of el.children) {
    out.push(c);
    out.push(...descendants(c));
  }
  return out;
}
function matchSelector(root, sel) {
  const steps = tokenize(sel);
  let anchors;
  if (steps[0].compound.scope) anchors = [root];
  else anchors = descendants(root).filter((d) => matchesCompound(d, steps[0].compound));
  for (let i = 1; i < steps.length; i++) {
    const step = steps[i];
    const next = [];
    for (const a of anchors) {
      const pool = step.combinator === ">" ? a.children : descendants(a);
      for (const n of pool) if (matchesCompound(n, step.compound) && !next.includes(n)) next.push(n);
    }
    anchors = next;
  }
  return anchors;
}

// Install the stub before importing the poller's DOM layer.
globalThis.document = { createElement: (t) => new El(t), createTextNode: (t) => mkText(t) };
const poller = await import("../../src/dev_helper_mcp/dashboard/static/poller.js");
const { updateFreshness, applyUnavailable, applyOrphans, applyEmptyStates, diff, patch } =
  poller.default;

const BASE = Date.parse("2026-06-26T11:00:00Z");

// ── builders mirroring render_board's structure (the parts the functions query) ──
function freshBoard() {
  const pg = new El("div");
  pg.className = "pg";
  const fresh = new El("span");
  fresh.className = "fresh";
  fresh.setAttribute("data-generated-at", "2026-06-26T11:00:00Z");
  fresh.setAttribute("data-stale-threshold-ms", "3000");
  fresh.textContent = "2026-06-26T11:00:00Z";
  pg.appendChild(fresh);
  return pg;
}
function wtLine(repo) {
  const wt = new El("div");
  wt.className = "wt";
  wt.setAttribute("data-repo", repo);
  const r = new El("span");
  r.className = "repo";
  r.textContent = repo;
  wt.appendChild(r);
  return wt;
}
function boardWithLines(repos) {
  const pg = new El("div");
  pg.className = "pg";
  for (const repo of repos) pg.appendChild(wtLine(repo));
  return pg;
}
function emptyBoard() {
  const pg = new El("div");
  pg.className = "pg";
  const eb = new El("div");
  eb.className = "empty-board";
  eb.setAttribute("hidden", "");
  pg.appendChild(eb);
  const col = new El("div");
  col.className = "col col-blk";
  const be = new El("div");
  be.className = "empty blk-empty";
  be.setAttribute("hidden", "");
  col.appendChild(be);
  pg.appendChild(col);
  return pg;
}

// ── UX-DR6/DR8: freshness stamp text + stale class swap ──

test("updateFreshness: under threshold → label only, no stale class/marker", () => {
  const pg = freshBoard();
  updateFreshness(pg, "2026-06-26T11:00:00Z", BASE + 1000);
  const f = pg.querySelector(".fresh");
  assert.equal(f.className, "fresh");
  assert.equal(f.textContent, "updated 1s ago");
  assert.equal(f.querySelector(".stale-marker"), null);
});

test("updateFreshness: over threshold → stale class + 'stale — git unavailable' marker", () => {
  const pg = freshBoard();
  updateFreshness(pg, "2026-06-26T11:00:00Z", BASE + 7000);
  const f = pg.querySelector(".fresh");
  assert.equal(f.className, "fresh stale");
  assert.match(f.textContent, /updated 7s ago/);
  assert.equal(f.querySelector(".stale-marker").textContent, "stale — git unavailable");
});

test("updateFreshness: an unparseable stamp leaves the server-rendered text untouched", () => {
  const pg = freshBoard();
  updateFreshness(pg, "", BASE + 7000);
  assert.equal(pg.querySelector(".fresh").textContent, "2026-06-26T11:00:00Z");
});

// ── UX-DR8: per-repo degrade, idempotent ──

test("applyUnavailable: marks only the repo_unavailable repo, leaves others normal", () => {
  const pg = boardWithLines(["/code/repoA", "/code/repoB"]);
  applyUnavailable(pg, ["repo_unavailable:/code/repoB"]);
  const a = pg.querySelectorAll(".wt[data-repo]")[0];
  const b = pg.querySelectorAll(".wt[data-repo]")[1];
  assert.equal(a.getAttribute("data-unavailable"), null, "repoA stays normal");
  assert.equal(b.getAttribute("data-unavailable"), "true");
  assert.equal(b.querySelector(".un").textContent, "· unavailable");
});

test("applyUnavailable: idempotent — re-applying the same warnings adds no duplicate marker", () => {
  const pg = boardWithLines(["/code/repoB"]);
  applyUnavailable(pg, ["repo_unavailable:/code/repoB"]);
  applyUnavailable(pg, ["repo_unavailable:/code/repoB"]);
  assert.equal(pg.querySelectorAll(".un").length, 1, "exactly one marker, not two");
});

test("applyUnavailable: a recovered repo clears the marker + attr", () => {
  const pg = boardWithLines(["/code/repoB"]);
  applyUnavailable(pg, ["repo_unavailable:/code/repoB"]);
  applyUnavailable(pg, []); // repo recovered this tick
  const b = pg.querySelector(".wt[data-repo]");
  assert.equal(b.getAttribute("data-unavailable"), null);
  assert.equal(pg.querySelectorAll(".un").length, 0);
});

// ── UX-DR7: orphan disclosure lifecycle + open-state preservation ──

test("applyOrphans: creates a collapsed disclosure (no open) when orphans appear", () => {
  const pg = new El("div");
  pg.className = "pg";
  applyOrphans(pg, ["orphan_link:s1@/code/r:agent/s1"]);
  const d = pg.querySelector(".fold.orphan");
  assert.ok(d, "orphan disclosure created");
  assert.equal(d.hasAttribute("open"), false, "never auto-expanded (UX-DR7)");
  assert.match(d.querySelector("summary").textContent, /1 orphaned annotation/);
  assert.match(
    d.querySelector(".body").textContent,
    /agent\/s1 — branch gone from git, note preserved here/,
  );
});

test("applyOrphans: removes the disclosure when orphans clear (zero-orphan omission)", () => {
  const pg = new El("div");
  pg.className = "pg";
  applyOrphans(pg, ["orphan_link:s1@/code/r:agent/s1"]);
  applyOrphans(pg, []);
  assert.equal(pg.querySelector(".fold.orphan"), null);
});

test("applyOrphans: a USER-OPENED disclosure survives a re-apply (open kept, same node)", () => {
  const pg = new El("div");
  pg.className = "pg";
  applyOrphans(pg, ["orphan_link:s1@/code/r:agent/s1"]);
  const d = pg.querySelector(".fold.orphan");
  d.setAttribute("open", ""); // the operator expands it
  d.__tag = "ORIGINAL";
  // A later poll re-applies the SAME orphans (e.g. only freshness changed).
  applyOrphans(pg, ["orphan_link:s1@/code/r:agent/s1"]);
  const again = pg.querySelector(".fold.orphan");
  assert.equal(again.__tag, "ORIGINAL", "same <details> node — never recreated");
  assert.equal(again.hasAttribute("open"), true, "the opened disclosure stays open across polls");
});

test("applyOrphans: a changed orphan set updates the body but keeps the node/open-state", () => {
  const pg = new El("div");
  pg.className = "pg";
  applyOrphans(pg, ["orphan_link:s1@/code/r:agent/s1"]);
  const d = pg.querySelector(".fold.orphan");
  d.setAttribute("open", "");
  d.__tag = "KEEP";
  applyOrphans(pg, ["orphan_link:s1@/code/r:agent/s1", "orphan_link:s2@/code/r:agent/s2"]);
  const again = pg.querySelector(".fold.orphan");
  assert.equal(again.__tag, "KEEP");
  assert.equal(again.hasAttribute("open"), true);
  assert.match(again.querySelector("summary").textContent, /2 orphaned annotations/);
});

// ── UX-DR9: empty-state copy shown/hidden by the live counts ──

test("applyEmptyStates: no active tasks → both empty copies shown", () => {
  const pg = emptyBoard();
  applyEmptyStates(pg, { tasks: [] });
  assert.equal(pg.querySelector(".empty-board").hasAttribute("hidden"), false);
  assert.equal(pg.querySelector(".col-blk .blk-empty").hasAttribute("hidden"), false);
});

test("applyEmptyStates: an active blocked task hides both empty copies", () => {
  const pg = emptyBoard();
  applyEmptyStates(pg, { tasks: [{ task_id: "b", status: "blocked" }] });
  assert.equal(pg.querySelector(".empty-board").hasAttribute("hidden"), true);
  assert.equal(pg.querySelector(".col-blk .blk-empty").hasAttribute("hidden"), true);
});

test("applyEmptyStates: a running task hides the board line but the empty Blocked copy shows", () => {
  const pg = emptyBoard();
  applyEmptyStates(pg, { tasks: [{ task_id: "r", status: "running" }] });
  assert.equal(pg.querySelector(".empty-board").hasAttribute("hidden"), true, "has an active task");
  assert.equal(
    pg.querySelector(".col-blk .blk-empty").hasAttribute("hidden"),
    false,
    "blocked is still empty → affirmative copy stays visible",
  );
});

// ── Code-review regression (HIGH): zero-done board ⇄ live done transition ──
// 2.4c omits the `<details class="fold done">` server-side when the loaded snapshot has
// zero done tasks (UX-DR9). The poller must therefore CREATE the done fold on demand when
// the first task transitions to done, and REMOVE it when the last done task leaves — else
// the card is dropped (regression caught in code review; patch_dom.test.mjs pre-builds the
// fold, so this start state was uncovered).

// A board skeleton WITHOUT a done fold — exactly what render_board emits at zero done.
function boardNoDoneFold() {
  const pg = new El("div");
  pg.className = "pg";
  const summary = new El("div");
  summary.className = "summary";
  for (const cls of ["run", "blk", "rev", "done"]) {
    const pill = new El("span");
    pill.className = "pill " + cls;
    const g = new El("span");
    g.className = "g";
    pill.appendChild(g);
    pill.appendChild(mkText("0 x"));
    summary.appendChild(pill);
  }
  pg.appendChild(summary);
  const cols = new El("div");
  cols.className = "cols";
  for (const cls of ["run", "blk", "rev"]) {
    const col = new El("div");
    col.className = "col col-" + cls;
    const h = new El("div");
    h.className = "col-h";
    const n = new El("span");
    n.className = "n";
    n.textContent = "0";
    h.appendChild(n);
    col.appendChild(h);
    cols.appendChild(col);
  }
  pg.appendChild(cols);
  return pg; // NO .fold.done (zero-done omission)
}
function snap(tasks) {
  return { generated_at: "t", tasks, warnings: [] };
}
function task(id, status) {
  return { task_id: id, description: "d", status, created_at: "t", updated_at: "t", worktrees: [] };
}
function findById(pg, id) {
  return pg.querySelectorAll("[data-task-id]").find((n) => n.getAttribute("data-task-id") === id);
}

test("a task transitioning to done on a zero-done board creates the fold (collapsed) — not dropped", () => {
  const pg = boardNoDoneFold();
  patch(pg, diff(snap([]), snap([task("x", "running")])), snap([task("x", "running")]));
  // x completes — the FIRST done task of the session.
  patch(pg, diff(snap([task("x", "running")]), snap([task("x", "done")])), snap([task("x", "done")]));
  const moved = findById(pg, "x");
  assert.ok(moved, "the completing card must NOT be dropped");
  const fold = pg.querySelector(".fold.done");
  assert.ok(fold, "the done disclosure is created on demand");
  assert.equal(fold.hasAttribute("open"), false, "created collapsed, never auto-expanded (UX-DR7)");
  assert.equal(moved.parentNode, fold.querySelector(".body"), "card lives in the done body");
  assert.equal(pg.querySelector(".fold.done > summary").textContent, "✓ 1 done");
});

test("a brand-new done task on a zero-done board also creates the fold", () => {
  const pg = boardNoDoneFold();
  patch(pg, diff(snap([]), snap([task("d", "done")])), snap([task("d", "done")]));
  assert.ok(findById(pg, "d"), "the new done card is placed, not dropped");
  assert.ok(pg.querySelector(".fold.done"), "fold created on demand");
});

test("the done fold is removed when the last done task leaves (matches a server reload)", () => {
  const pg = boardNoDoneFold();
  patch(pg, diff(snap([]), snap([task("x", "done")])), snap([task("x", "done")]));
  assert.ok(pg.querySelector(".fold.done"), "fold present while a done task exists");
  // x is removed from the snapshot entirely.
  patch(pg, diff(snap([task("x", "done")]), snap([])), snap([]));
  assert.equal(pg.querySelector(".fold.done"), null, "fold removed at zero done (UX-DR9 omission)");
  assert.equal(findById(pg, "x"), undefined, "the card is gone with it");
});

test("an opened on-demand done fold keeps its open-state across a later done add", () => {
  const pg = boardNoDoneFold();
  patch(pg, diff(snap([]), snap([task("a", "done")])), snap([task("a", "done")]));
  const fold = pg.querySelector(".fold.done");
  fold.setAttribute("open", ""); // operator expands it
  fold.__tag = "ORIGINAL";
  // a second task completes
  patch(
    pg,
    diff(snap([task("a", "done")]), snap([task("a", "done"), task("b", "done")])),
    snap([task("a", "done"), task("b", "done")]),
  );
  const again = pg.querySelector(".fold.done");
  assert.equal(again.__tag, "ORIGINAL", "same fold node — not recreated");
  assert.equal(again.hasAttribute("open"), true, "the opened disclosure stays open");
  assert.equal(again.querySelector("summary").textContent, "✓ 2 done");
});
