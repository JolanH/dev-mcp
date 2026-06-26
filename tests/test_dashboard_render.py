"""Story 2.4a — HTML-output assertions over the pure ``render_board`` (Decision A/C).

Browser-free: a synchronous pytest renders ``render_board(fixed_dict)`` and parses the
output with a tiny **stdlib** ``html.parser.HTMLParser``-based DOM (Decision C — no
``selectolax``/new dep). Plus one async ``GET /`` read-only smoke over the in-process
ASGI client. No git, no server, no clock — the renderer is pure and total.

Coverage:
* AC1/UX-DR1/13 — exactly 3 active columns, grouped by task (one card, N ``.wt`` lines),
  ``done`` only inside the ``<details class="fold done">``, never a column.
* AC1/UX-DR2 — one summary pill per status incl. done; pill counts == rendered column
  card counts == done-disclosure count; zero-count pills still render.
* AC2/UX-DR3 — every status node carries ``data-status`` + a matching per-card glyph;
  badges "needs input"/"awaiting review"; no "merge" anywhere.
* AC2/UX-DR4 — blocked card lifted; running/review flat; done dimmed.
* FR-10 — ``GET /`` → 200 text/html, no ``<form>``/``<button>``/mutating control.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

from dev_helper_mcp.dashboard import tokens
from dev_helper_mcp.dashboard.render import render_board

_VOID = {"meta", "br", "img", "input", "hr", "link", "area", "base", "col", "wbr"}


class _Node:
    """A minimal DOM node: tag + attrs + children + recursive text."""

    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node] = []
        # Ordered content: strings (text nodes) and child _Nodes interleaved in
        # document order, so .text reads exactly as the source (glyph before count).
        self._content: list[str | _Node] = []

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    @property
    def text(self) -> str:
        return "".join(part if isinstance(part, str) else part.text for part in self._content)

    def find_all(self, tag: str | None = None, class_: str | None = None) -> list[_Node]:
        found: list[_Node] = []
        for c in self.children:
            if (tag is None or c.tag == tag) and (class_ is None or class_ in c.classes):
                found.append(c)
            found.extend(c.find_all(tag, class_))
        return found

    def find_attr(self, attr: str) -> list[_Node]:
        """All descendants (and self if matching) carrying ``attr``."""
        found: list[_Node] = []
        if attr in self.attrs:
            found.append(self)
        for c in self.children:
            found.extend(c.find_attr(attr))
        return found


class _DOM(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self._stack = [self.root]

    def _add(self, node):
        parent = self._stack[-1]
        parent.children.append(node)
        parent._content.append(node)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self._add(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):  # <tag .../>
        self._add(_Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        self._stack[-1]._content.append(data)


def parse(html: str) -> _Node:
    dom = _DOM()
    dom.feed(html)
    return dom.root


def _pill_count_label(pill: _Node) -> tuple[int, str]:
    """Parse a pill's text ``"●1 running"`` → ``(1, "running")`` (glyph is glued to
    the count per the mock; label is the trailing word)."""
    m = re.search(r"(\d+)\s+(\w+)", pill.text)
    assert m, f"unparseable pill text {pill.text!r}"
    return int(m.group(1)), m.group(2)


# ── Fixtures: fixed payload dicts (the asdict(CacheSnapshot) shape) ──


def _wt(repo: str, slug: str, *, orphaned: bool = False) -> dict:
    return {
        "repo_path": repo,
        "branch": f"agent/{slug}",
        "path": None if orphaned else f"{repo}.worktrees/{slug}",
        "head": None if orphaned else "abc1234",
        "detached": False,
        "locked": False,
        "prunable": False,
        "orphaned": orphaned,
    }


def _task(slug: str, status: str | None, worktrees: list[dict]) -> dict:
    return {
        "task_id": slug,
        "description": f"{slug} description",
        "status": status,
        "created_at": "2026-06-26T10:00:00Z",
        "updated_at": "2026-06-26T10:30:00Z",
        "worktrees": worktrees,
    }


def _snapshot(tasks: list[dict], generated_at: str = "2026-06-26T11:00:00Z") -> dict:
    return {"generated_at": generated_at, "tasks": tasks, "warnings": []}


# A representative happy-path board: 2 running (one multi-repo), 1 blocked, 1 review,
# 2 done. Deliberately NO "merge" anywhere in the slugs/branches.
def _board_payload() -> dict:
    return _snapshot(
        [
            _task(
                "api-refactor",
                "running",
                [_wt("/code/repoA", "api-refactor"), _wt("/code/repoB", "api-refactor")],
            ),
            _task("auth-docs", "done", [_wt("/code/repoC", "auth-docs")]),
            _task("db-migration", "blocked", [_wt("/code/repoB", "db-migration")]),
            _task("lint-cleanup", "done", [_wt("/code/repoA", "lint-cleanup")]),
            _task(
                "payments-api",
                "review",
                [_wt("/code/repoA", "payments-api"), _wt("/code/repoC", "payments-api")],
            ),
            _task("ui-polling", "running", [_wt("/code/repoA", "ui-polling")]),
        ]
    )


# ── AC1 / UX-DR1 / UX-DR13: 3 columns, by-task grouping, done in the disclosure ──


def test_exactly_three_active_columns_in_lifecycle_order():
    root = parse(render_board(_board_payload()))
    cols = root.find_all("div", "col")
    assert len(cols) == 3, "the board must have exactly three active columns"
    # Lifecycle order Running | Blocked | Review, encoded as col-run/col-blk/col-rev.
    col_kinds = [next(c for c in col.classes if c.startswith("col-")) for col in cols]
    assert col_kinds == ["col-run", "col-blk", "col-rev"]


def test_done_is_a_disclosure_not_a_column():
    root = parse(render_board(_board_payload()))
    # No board column carries a done node. The fixture DOES place running/blocked/review
    # cards in .cols, so this iterates real status nodes — the check is non-vacuous.
    cols_container = root.find_all("div", "cols")[0]
    col_statuses = [n.attrs.get("data-status") for n in cols_container.find_attr("data-status")]
    assert col_statuses, "columns must contain status nodes (else this check is vacuous)"
    assert "done" not in col_statuses, "no done node may appear in a board column"
    # The done-disclosure is a collapsed <details> (no `open`) holding the done nodes.
    details = root.find_all("details")
    assert len(details) == 1
    done_fold = details[0]
    assert "done" in done_fold.classes
    assert "open" not in done_fold.attrs, "the done-disclosure must be collapsed by default"
    done_nodes = [n for n in done_fold.find_attr("data-status") if n.attrs["data-status"] == "done"]
    assert len(done_nodes) == 2, "both done tasks live inside the disclosure"


def test_grouping_by_task_one_card_n_worktree_lines():
    root = parse(render_board(_board_payload()))
    # api-refactor spans repoA + repoB → exactly ONE card with TWO .wt lines.
    cards = [
        c for c in root.find_all("div", "card") if c.attrs.get("data-task-id") == "api-refactor"
    ]
    assert len(cards) == 1, "a multi-repo task is ONE card, never N cards"
    wts = cards[0].find_all("div", "wt")
    assert len(wts) == 2
    assert "repoA" in wts[0].text and "repoB" in wts[1].text


# ── AC1 / UX-DR2: summary pills agree with the board; zero-counts render ──


def test_summary_pills_one_per_status_counts_agree_and_zero_renders():
    # 1 running, 0 blocked, 1 review, 0 done — forces two zero-count pills.
    payload = _snapshot(
        [
            _task("solo-run", "running", [_wt("/code/r", "solo-run")]),
            _task("solo-rev", "review", [_wt("/code/r", "solo-rev")]),
        ]
    )
    root = parse(render_board(payload))
    pills = root.find_all("span", "pill")
    assert len(pills) == 4, "one pill per status (running, blocked, review, done)"

    # Map pill -> (count, label) by parsing "●1 running".
    pill_text = {label: count for count, label in (_pill_count_label(p) for p in pills)}
    assert pill_text == {"running": 1, "blocked": 0, "review": 1, "done": 0}
    # Zero-count pills are present and legible.
    assert any("0 blocked" in p.text for p in pills)
    assert any("0 done" in p.text for p in pills)

    # Pill counts equal the rendered column card counts.
    cols = root.find_all("div", "col")
    by_kind = {next(c for c in col.classes if c.startswith("col-")): col for col in cols}
    assert len(by_kind["col-run"].find_all("div", "card")) == pill_text["running"]
    assert len(by_kind["col-blk"].find_all("div", "card")) == pill_text["blocked"]
    assert len(by_kind["col-rev"].find_all("div", "card")) == pill_text["review"]


def test_done_pill_count_equals_disclosure_count():
    root = parse(render_board(_board_payload()))
    pills = root.find_all("span", "pill")
    done_pill = next(p for p in pills if _pill_count_label(p)[1] == "done")
    done_pill_count = _pill_count_label(done_pill)[0]
    details = root.find_all("details")[0]
    done_nodes = [n for n in details.find_attr("data-status") if n.attrs["data-status"] == "done"]
    assert done_pill_count == len(done_nodes) == 2
    # Summary text "✓ 2 done" agrees with the disclosure's "✓ 2 done" summary.
    summary = details.find_all("summary")[0]
    assert "2 done" in summary.text


# ── AC2 / UX-DR3: per-card non-color encoding + badges + no "merge" ──


def test_every_status_node_has_data_status_and_matching_glyph():
    root = parse(render_board(_board_payload()))
    status_nodes = root.find_attr("data-status")
    assert status_nodes, "there must be status-bearing nodes"
    for node in status_nodes:
        status = node.attrs["data-status"]
        assert status in tokens.STATUS_GLYPH, f"unknown data-status {status!r}"
        # The per-card glyph travels ON the card (color-blind channel), not just the header.
        assert tokens.STATUS_GLYPH[status] in node.text, f"{status} card missing its glyph"


def test_reason_badges_blocked_and_review():
    root = parse(render_board(_board_payload()))
    blk = next(c for c in root.find_all("div", "card") if c.attrs.get("data-status") == "blocked")
    rev = next(c for c in root.find_all("div", "card") if c.attrs.get("data-status") == "review")
    run = next(c for c in root.find_all("div", "card") if c.attrs.get("data-status") == "running")
    assert "needs input" in blk.text
    assert "awaiting review" in rev.text
    assert run.find_all("span", "badge") == [], "running carries no reason badge"


def test_no_merge_anywhere_in_the_document():
    html = render_board(_board_payload())
    assert "merge" not in html.lower(), "review is 'awaiting review', never 'merge'"


# ── AC2 / UX-DR4: emphasis — blocked lifted, running/review flat, done dimmed ──


def test_blocked_is_the_only_lifted_card():
    html = render_board(_board_payload())
    root = parse(html)
    blk = next(c for c in root.find_all("div", "card") if c.attrs.get("data-status") == "blocked")
    run = next(c for c in root.find_all("div", "card") if c.attrs.get("data-status") == "running")
    rev = next(c for c in root.find_all("div", "card") if c.attrs.get("data-status") == "review")
    # The lift is the .blk class (the amber ring/bloom rule keys off it); running/review
    # do not carry it, so they stay on the flat baseline.
    assert "blk" in blk.classes
    assert "blk" not in run.classes and "blk" not in rev.classes
    # The CSS encodes the lift on .card.blk and the dimming on .donecard (no motion).
    assert ".card.blk{" in html and "box-shadow:0 0 0 1px var(--blk-bd)" in html
    assert ".donecard{ opacity:.55" in html


# ── FR-10: GET / is read-only HTML ──


def test_get_root_serves_read_only_html(app, asgi_client_factory):
    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as client:
                return await client.get("/")

    resp = asyncio.run(_run())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # No mutating control of any kind (read-only forever).
    low = body.lower()
    assert "<form" not in low
    assert "<button" not in low
    assert "<input" not in low
    # It is the board page.
    assert "<title>dev-helper-mcp</title>" in body
    assert 'class="cols"' in body
