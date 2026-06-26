"""Story 2.4a — overflow contract over the rendered inline CSS (UX-DR12).

The board never scrolls horizontally; an over-full active column scrolls within
itself; the 3-column grid collapses to one column at a narrow width (static layout,
not motion — UX-DR4-safe). The reference mock omits these; 2.4a ADDS them. Pure
string assertions over the inline ``<style>`` (no parser/dep).
"""

from __future__ import annotations

import re

from dev_helper_mcp.dashboard.render import render_board

HTML = render_board({"generated_at": "2026-06-26T11:00:00Z", "tasks": [], "warnings": []})


def _rule(selector: str) -> str:
    """Return the declaration block for the first ``{selector}{…}`` in the CSS."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", HTML)
    assert m, f"no CSS rule found for selector {selector!r}"
    return m.group(1)


def test_board_never_scrolls_horizontally():
    cols = _rule(".cols")
    assert re.search(r"overflow-x\s*:\s*(hidden|clip|none)", cols), (
        ".cols must set overflow-x hidden/clip — the board never scrolls horizontally"
    )


def test_column_scrolls_within_itself():
    col = _rule(".col")
    assert re.search(r"overflow-y\s*:\s*auto", col), (
        ".col must scroll within itself (overflow-y:auto)"
    )
    assert "max-height" in col, ".col needs a bounded max-height so it can scroll within itself"


def test_grid_collapses_to_one_column_at_narrow_width():
    # A max-width media query collapses the 3-col grid to a single column.
    m = re.search(r"@media\s*\(max-width:\s*\d+px\)\s*\{\s*\.cols\s*\{([^}]*)\}", HTML)
    assert m, "a max-width media query must collapse .cols"
    assert re.search(r"grid-template-columns\s*:\s*1fr", m.group(1)), "3->1 wrap at narrow width"
