"""Story 2.4c — HTML-output assertions for freshness / degraded / orphan / empty states.

Browser-free, per Decision B: the degrade/empty/orphan treatments + the stale-at-load
class live in the **Python** ``render_board`` and are asserted by parsing the served HTML
with the stdlib parser (reused from ``test_dashboard_render``). The live-freshness time
logic (UX-DR6) is unit-tested in ``tests/js/staleness.test.mjs`` (``node --test``); here we
inject a deterministic ``now_ms`` so the server's stale-at-load render has an HTML assertion
too. All payloads are hand-built dicts — zero git surface (git-safety HARD RULE).

Coverage (AC1–4 / UX-DR6/7/8/9):
* AC1 — old ``generated_at`` + injected ``now`` → ``.fresh`` carries ``stale`` + the marker;
  recent ``now`` → no stale class; default (no ``now_ms``) → raw stamp, no stale class.
* AC2 — Done + orphan ``<details>`` below the board, NEITHER ``open``, self-explaining; zero
  done → no done disclosure; zero orphan → no orphan disclosure.
* AC3 — ``repo_unavailable:`` marks only that repo's worktree lines "unavailable"; others
  render normally; a stale board still shows its cards (never blank) + the marker.
* AC4 — empty Blocked → "Nothing needs you"; empty Running/Review → header + "0", no card;
  fully empty board → the create-one line; that line is hidden when active tasks exist.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Reuse the tiny stdlib-parser DOM from the 2.4a render tests (no new dep, Decision C).
sys.path.insert(0, str(Path(__file__).parent))
from test_dashboard_render import parse  # noqa: E402

from dev_helper_mcp.dashboard.render import render_board  # noqa: E402

# A fixed base instant so injected-``now`` tests never read a real clock.
_BASE_MS = int(datetime.fromisoformat("2026-06-26T11:00:00Z").timestamp() * 1000)
_GEN_AT = "2026-06-26T11:00:00Z"
# threshold = DASHBOARD_POLL_INTERVAL_MS(1500) * DASHBOARD_STALE_FACTOR(2) = 3000ms
_OVER = _BASE_MS + 7000  # 7s old → stale
_UNDER = _BASE_MS + 1000  # 1s old → fresh


# ── payload builders (hand-built dicts; no git) ──


def _wt(repo: str, slug: str = "t") -> dict:
    return {
        "repo_path": repo,
        "branch": f"agent/{slug}",
        "path": f"{repo}.worktrees/{slug}",
        "head": "abc1234",
        "detached": False,
        "locked": False,
        "prunable": False,
        "orphaned": False,
    }


def _task(slug: str, status: str, worktrees: list[dict] | None = None) -> dict:
    return {
        "task_id": slug,
        "description": "d",
        "status": status,
        "created_at": "2026-06-26T10:00:00Z",
        "updated_at": "2026-06-26T10:30:00Z",
        "worktrees": worktrees or [],
    }


def _snapshot(tasks: list[dict], warnings: list[str] | None = None, gen: str = _GEN_AT) -> dict:
    return {"generated_at": gen, "tasks": tasks, "warnings": warnings or []}


def _fresh(root) -> object:
    return root.find_all("span", "fresh")[0]


def _details_by_class(root, cls: str) -> list:
    return [d for d in root.find_all("details") if cls in d.classes]


# ── AC1 / UX-DR6: server-rendered stale-at-load treatment (injected now) ──


def test_stale_at_load_emits_stale_class_and_marker_when_now_injected():
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])]), now_ms=_OVER))
    fresh = _fresh(root)
    assert "stale" in fresh.classes, "an aged stamp + injected now must carry the stale class"
    assert "stale — git unavailable" in fresh.text, "the explicit degrade marker (UX-DR8)"
    assert "updated 7s ago" in fresh.text, "the relative-age label still shows"


def test_under_threshold_with_injected_now_is_not_stale():
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])]), now_ms=_UNDER))
    fresh = _fresh(root)
    assert "stale" not in fresh.classes
    assert "git unavailable" not in fresh.text
    assert "updated 1s ago" in fresh.text


def test_no_now_injected_renders_raw_stamp_no_stale_class():
    # Production path: the route passes no now_ms → the renderer reads NO clock; the raw
    # stamp ships and the client computes staleness live (Decision A).
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])])))
    fresh = _fresh(root)
    assert "stale" not in fresh.classes
    assert _GEN_AT in fresh.text
    # The client needs the threshold to compute staleness live.
    assert fresh.attrs.get("data-stale-threshold-ms") == "3000"
    assert fresh.attrs.get("data-generated-at") == _GEN_AT


# ── AC2 / UX-DR7: Done + orphan disclosures — collapsed, never open, self-explaining ──


def test_done_and_orphan_disclosures_present_collapsed_and_self_explaining():
    payload = _snapshot(
        [_task("run-1", "running", [_wt("/code/r")]), _task("done-1", "done", [_wt("/code/r")])],
        warnings=["orphan_link:old-spike@/code/repoA:agent/old-spike"],
    )
    root = parse(render_board(payload))
    done = _details_by_class(root, "done")
    orphan = _details_by_class(root, "orphan")
    assert len(done) == 1 and len(orphan) == 1, "both disclosures render below the board"
    # NEITHER auto-expanded, even though both are non-empty (UX-DR7).
    assert "open" not in done[0].attrs, "done disclosure must be collapsed"
    assert "open" not in orphan[0].attrs, "orphan disclosure must be collapsed"
    # Self-explaining summaries + body line.
    assert "1 done" in done[0].find_all("summary")[0].text
    assert "1 orphaned annotation" in orphan[0].find_all("summary")[0].text
    body = orphan[0].find_all("div", "o")[0]
    assert "agent/old-spike" in body.text
    assert "branch gone from git, note preserved here" in body.text


def test_plural_orphan_summary_counts():
    payload = _snapshot(
        [_task("a", "running", [_wt("/code/r")])],
        warnings=[
            "orphan_link:s1@/code/repoA:agent/s1",
            "orphan_link:s2@/code/repoB:agent/s2",
        ],
    )
    root = parse(render_board(payload))
    orphan = _details_by_class(root, "orphan")[0]
    assert "2 orphaned annotations" in orphan.find_all("summary")[0].text


def test_zero_done_omits_the_done_disclosure():
    # Only active tasks, no done → NO done <details> (UX-DR9 zero-done case).
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])])))
    assert _details_by_class(root, "done") == [], "zero done must omit the disclosure"


def test_zero_orphan_omits_the_orphan_disclosure():
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])])))
    assert _details_by_class(root, "orphan") == [], "zero orphan must omit the disclosure"


# ── AC3 / UX-DR8: per-repo degrade + never-blank stale board ──


def test_repo_unavailable_marks_only_that_repos_lines():
    payload = _snapshot(
        [
            _task(
                "multi",
                "running",
                [_wt("/code/repoA", "multi"), _wt("/code/repoB", "multi")],
            )
        ],
        warnings=["repo_unavailable:/code/repoB"],
    )
    root = parse(render_board(payload))
    lines = {wt.attrs.get("data-repo"): wt for wt in root.find_all("div", "wt")}
    repo_a, repo_b = lines["/code/repoA"], lines["/code/repoB"]
    # repoB degraded, repoA normal — a single slow repo never blanks the board.
    assert repo_b.attrs.get("data-unavailable") == "true"
    assert "unavailable" in repo_b.text
    assert "data-unavailable" not in repo_a.attrs, "other repos render normally"
    assert "unavailable" not in repo_a.text


def test_stale_board_is_never_blank_still_shows_cards():
    # Whole-board stale (old generated_at, total-git-failure carry-forward): the marker is
    # present AND the last-known cards still render (UX-DR8 "never blank").
    payload = _snapshot(
        [_task("alive", "running", [_wt("/code/r")]), _task("blk", "blocked", [_wt("/code/r")])]
    )
    root = parse(render_board(payload, now_ms=_OVER))
    assert "stale — git unavailable" in _fresh(root).text
    cards = {c.attrs.get("data-task-id") for c in root.find_all("div", "card")}
    assert {"alive", "blk"} <= cards, "a stale board still shows its last-known cards"


# ── AC4 / UX-DR9: empty states ──


def test_empty_blocked_column_reads_nothing_needs_you():
    # running present, blocked empty → the Blocked column shows the affirmative copy.
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])])))
    blk_col = next(c for c in root.find_all("div", "col") if "col-blk" in c.classes)
    empties = blk_col.find_all("div", "blk-empty")
    assert len(empties) == 1
    assert "Nothing needs you" in empties[0].text
    assert "hidden" not in empties[0].attrs, "shown when the blocked column is empty"


def test_nonempty_blocked_hides_nothing_needs_you():
    root = parse(render_board(_snapshot([_task("b", "blocked", [_wt("/code/r")])])))
    blk_col = next(c for c in root.find_all("div", "col") if "col-blk" in c.classes)
    empty = blk_col.find_all("div", "blk-empty")[0]
    assert "hidden" in empty.attrs, "the affirmative copy is hidden when a blocked card exists"


def test_empty_active_columns_show_header_count_zero_no_placeholder_card():
    # Only a review task → running + blocked empty; each shows header + "0", no .card.
    root = parse(render_board(_snapshot([_task("rv", "review", [_wt("/code/r")])])))
    for kind, expected in (("col-run", "0"), ("col-blk", "0")):
        col = next(c for c in root.find_all("div", "col") if kind in c.classes)
        assert col.find_all("span", "n")[0].text == expected
        assert col.find_all("div", "card") == [], f"{kind} must have no placeholder card"


def test_empty_running_review_columns_have_no_affirmative_copy():
    # Only Blocked gets "Nothing needs you"; running/review stay quiet (no .empty element).
    root = parse(render_board(_snapshot([_task("b", "blocked", [_wt("/code/r")])])))
    for kind in ("col-run", "col-rev"):
        col = next(c for c in root.find_all("div", "col") if kind in c.classes)
        assert col.find_all("div", "empty") == [], f"{kind} must not carry empty copy"


def test_fully_empty_board_shows_create_one_line():
    root = parse(render_board(_snapshot([])))
    line = root.find_all("div", "empty-board")
    assert len(line) == 1
    assert "hidden" not in line[0].attrs, "shown when there are no active tasks"
    assert "No active tasks — create one with create_task" in line[0].text
    # The `create_task` is code-styled (backtick → <code>).
    assert line[0].find_all("code")[0].text == "create_task"


def test_empty_board_line_hidden_when_active_tasks_exist():
    root = parse(render_board(_snapshot([_task("a", "running", [_wt("/code/r")])])))
    line = root.find_all("div", "empty-board")[0]
    assert "hidden" in line.attrs, "the create-one line is hidden while active tasks exist"


def test_done_only_board_is_not_fully_empty():
    # A board with only done tasks has no ACTIVE tasks → the create-one line shows, and the
    # done disclosure renders. (active_count counts only running/blocked/review.)
    root = parse(render_board(_snapshot([_task("d", "done", [_wt("/code/r")])])))
    assert "hidden" not in root.find_all("div", "empty-board")[0].attrs
    assert len(_details_by_class(root, "done")) == 1
