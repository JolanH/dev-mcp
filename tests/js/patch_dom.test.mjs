// Story 2.4b — DOM patch-layer coverage (code-review follow-up, 2026-06-26).
//
// `diff.test.mjs` covers the pure core; `patch_spy.test.mjs` proves "empty diff ⇒ no
// patch call". Neither exercised the DOM layer (`patch`/`reparent`/`insertSorted`/
// `updateCounts`/`renderCard`) — the riskiest code. Per Decision D (no jsdom, minimal
// deps), this test installs a TINY hand-rolled `document` stub (just enough DOM + the
// handful of selectors the poller uses) and drives the REAL `patch` against it.
//
// What it asserts (the AC3 / robustness predicates that were untested):
//   * add places a card in its task_id-sorted slot in the right column;
//   * a status change REPARENTS the SAME node object (identity preserved) into the new
//     column / the done disclosure — never destroy-and-recreate;
//   * a field-only worktree change rebuilds only the `.wt` lines, node identity kept;
//   * column-header counts, summary pills, and the "✓ N done" summary track `next`;
//   * an unknown/null status removes the node (the code-review P2 fix), matching what a
//     server reload would render (it places only the four known statuses).

import test from "node:test";
import assert from "node:assert/strict";

// ── Minimal DOM stub ─────────────────────────────────────────────────────────
// One node class (elements + text nodes) and a querySelector(All) supporting exactly
// the selector grammar the poller uses: compound selectors (tag + .class* + [attr])
// joined by descendant (' ') or child ('>') combinators, with an optional leading
// `:scope`. Pre-order traversal = document order.

class El {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.nodeType = tag ? 1 : 3; // no tag ⇒ text node
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
  insertBefore(node, ref) {
    if (!ref) return this.appendChild(node);
    if (node.parentNode) node.parentNode._detach(node);
    node.parentNode = this;
    const i = this.childNodes.indexOf(ref);
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, node);
    return node;
  }
  remove() {
    if (this.parentNode) this.parentNode._detach(this);
  }
  replaceChildren() {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
  }
  get nextSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.childNodes.indexOf(this);
    return this.parentNode.childNodes[i + 1] || null;
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
  let start;
  if (steps[0].compound.scope) {
    anchors = [root];
    start = 1;
  } else {
    anchors = descendants(root).filter((d) => matchesCompound(d, steps[0].compound));
    start = 1;
  }
  for (let i = start; i < steps.length; i++) {
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

// ── Build a board skeleton mirroring render_board's structure (the parts patch queries) ──

function buildSkeleton() {
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

  const fold = new El("details");
  fold.className = "fold done";
  const sum = new El("summary");
  sum.textContent = "✓ 0 done";
  const body = new El("div");
  body.className = "body";
  fold.appendChild(sum);
  fold.appendChild(body);
  pg.appendChild(fold);

  return pg;
}

function mkText(v) {
  const t = new El();
  t._text = String(v);
  return t;
}

// ── snapshot helpers ──
function wt(repo, branch, path) {
  return { repo_path: repo, branch, path, head: "a", detached: false, locked: false, prunable: false, orphaned: false };
}
function task(id, status, worktrees = []) {
  return { task_id: id, description: "d", status, created_at: "t", updated_at: "t", worktrees };
}
function snap(tasks) {
  return { generated_at: "t", tasks, warnings: [] };
}
function ids(containerEl) {
  return containerEl.querySelectorAll(":scope > [data-task-id]").map((c) => c.getAttribute("data-task-id"));
}

// Install the stub document before importing the poller's DOM layer.
globalThis.document = { createElement: (t) => new El(t), createTextNode: (t) => mkText(t) };
const poller = await import("../../src/dev_helper_mcp/dashboard/static/poller.js");
const { diff, patch } = poller.default;

function seededBoard(tasks) {
  const board = buildSkeleton();
  const s = snap(tasks);
  patch(board, diff(snap([]), s), s); // populate via the real add path
  return board;
}
function col(board, cls) {
  return board.querySelector(".col-" + cls);
}
function doneBody(board) {
  return board.querySelector(".fold.done .body");
}
function find(board, id) {
  return board.querySelectorAll("[data-task-id]").find((n) => n.getAttribute("data-task-id") === id) || null;
}

// ── Tests ──

test("add: cards land in the correct column, in task_id-sorted order", () => {
  const board = seededBoard([task("zebra", "running"), task("alpha", "running"), task("mango", "blocked")]);
  assert.deepEqual(ids(col(board, "run")), ["alpha", "zebra"]);
  assert.deepEqual(ids(col(board, "blk")), ["mango"]);
  assert.deepEqual(ids(col(board, "rev")), []);
});

test("add: a new card is inserted at its sorted slot between existing siblings", () => {
  const board = seededBoard([task("a", "running"), task("c", "running")]);
  const next = snap([task("a", "running"), task("b", "running"), task("c", "running")]);
  patch(board, diff(snap([task("a", "running"), task("c", "running")]), next), next);
  assert.deepEqual(ids(col(board, "run")), ["a", "b", "c"]);
});

test("status change REPARENTS the same node object (identity preserved), not recreated", () => {
  const board = seededBoard([task("x", "running")]);
  const node = find(board, "x");
  node.__tag = "ORIGINAL"; // tag the live node
  const prev = snap([task("x", "running")]);
  const next = snap([task("x", "review")]);
  patch(board, diff(prev, next), next);
  assert.equal(ids(col(board, "run")).length, 0, "left the running column");
  const moved = find(board, "x");
  assert.equal(moved.__tag, "ORIGINAL", "the SAME node moved — not destroyed/recreated");
  assert.equal(moved.parentNode, col(board, "rev"), "now in the review column");
  assert.equal(moved.getAttribute("data-status"), "review");
});

test("status change to done reparents the same node into the done disclosure body", () => {
  const board = seededBoard([task("x", "running")]);
  const node = find(board, "x");
  node.__tag = "SAME";
  const next = snap([task("x", "done")]);
  patch(board, diff(snap([task("x", "running")]), next), next);
  const moved = find(board, "x");
  assert.equal(moved.__tag, "SAME");
  assert.equal(moved.parentNode, doneBody(board), "moved into the done disclosure body");
  assert.ok(moved.className.includes("donecard"), "re-shaped to the donecard markup");
});

test("unknown/null status removes the node (matches a server reload that skips it)", () => {
  const board = seededBoard([task("x", "running")]);
  const next = snap([task("x", null)]);
  patch(board, diff(snap([task("x", "running")]), next), next);
  assert.equal(find(board, "x"), null, "an unrenderable-status task is removed, not left as a ghost");
});

test("field-only worktree change rebuilds .wt lines, preserves node identity & column", () => {
  const board = seededBoard([task("x", "running", [wt("/code/a", "agent/x", "/a")])]);
  const node = find(board, "x");
  node.__tag = "KEEP";
  const next = snap([task("x", "running", [wt("/code/a", "agent/x", "/a"), wt("/code/b", "agent/x", "/b")])]);
  patch(board, diff(snap([task("x", "running", [wt("/code/a", "agent/x", "/a")])]), next), next);
  const same = find(board, "x");
  assert.equal(same.__tag, "KEEP", "node identity preserved on a field-only update");
  assert.equal(same.parentNode, col(board, "run"), "stayed in its column");
  assert.equal(same.querySelectorAll(".wt").length, 2, "the .wt lines were rebuilt to match next");
});

test("remove: a vanished task's node is removed", () => {
  const board = seededBoard([task("a", "running"), task("b", "running")]);
  const next = snap([task("a", "running")]);
  patch(board, diff(snap([task("a", "running"), task("b", "running")]), next), next);
  assert.equal(find(board, "b"), null);
  assert.deepEqual(ids(col(board, "run")), ["a"]);
});

test("counts: column headers, pills, and the done summary track next", () => {
  const board = seededBoard([task("a", "running"), task("b", "blocked"), task("c", "done")]);
  assert.equal(col(board, "run").querySelector(".col-h .n").textContent, "1");
  assert.equal(col(board, "blk").querySelector(".col-h .n").textContent, "1");
  assert.equal(col(board, "rev").querySelector(".col-h .n").textContent, "0");
  assert.equal(board.querySelector(".fold.done > summary").textContent, "✓ 1 done");
  // a status move updates the affected counts
  const next = snap([task("a", "review"), task("b", "blocked"), task("c", "done")]);
  patch(board, diff(snap([task("a", "running"), task("b", "blocked"), task("c", "done")]), next), next);
  assert.equal(col(board, "run").querySelector(".col-h .n").textContent, "0");
  assert.equal(col(board, "rev").querySelector(".col-h .n").textContent, "1");
});
