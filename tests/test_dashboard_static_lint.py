"""Story 2.4a — static CSS/JS lint over the rendered board (UX-DR4 + UX-DR10).

Pure string grep over ``render_board(...)`` output (no parser, no dep):
* **No motion (UX-DR4):** absent ``transition`` / ``animation`` / ``@keyframes`` /
  ``scroll-behavior:smooth`` in CSS; no ``requestAnimationFrame`` / timer-driven style
  mutation in JS (2.4a ships no JS, but the grep guards the page from the start — it
  bites in 2.4b/c).
* **Self-contained (UX-DR10):** all CSS/JS inline; no external asset — no
  ``http(s)://``, no protocol-relative ``//``, no ``<link … href>``, no ``<script … src>``,
  no ``@import``, no ``url(http…)``; system font stack only.
"""

from __future__ import annotations

from dev_helper_mcp.dashboard.render import render_board


def _payload() -> dict:
    return {
        "generated_at": "2026-06-26T11:00:00Z",
        "warnings": [],
        "tasks": [
            {
                "task_id": "api-refactor",
                "description": "d",
                "status": "running",
                "created_at": "2026-06-26T10:00:00Z",
                "updated_at": "2026-06-26T10:30:00Z",
                "worktrees": [
                    {
                        "repo_path": "/code/repoA",
                        "branch": "agent/api-refactor",
                        "path": "/code/repoA.worktrees/api-refactor",
                        "head": "abc",
                        "detached": False,
                        "locked": False,
                        "prunable": False,
                        "orphaned": False,
                    }
                ],
            },
            {
                "task_id": "db-migration",
                "description": "d",
                "status": "blocked",
                "created_at": "2026-06-26T10:00:00Z",
                "updated_at": "2026-06-26T10:30:00Z",
                "worktrees": [],
            },
            {
                "task_id": "auth-docs",
                "description": "d",
                "status": "done",
                "created_at": "2026-06-26T10:00:00Z",
                "updated_at": "2026-06-26T10:30:00Z",
                "worktrees": [],
            },
        ],
    }


HTML = render_board(_payload())
LOW = HTML.lower()


# ── UX-DR4: no motion ──


def test_no_motion_tokens():
    for token in ("transition", "animation", "@keyframes", "scroll-behavior:smooth"):
        assert token not in LOW, f"forbidden motion token present: {token!r}"


def test_no_timer_driven_style_mutation():
    # 2.4a serves no JS poller (that is 2.4b); the page must be correct with JS disabled.
    for token in ("requestanimationframe", "setinterval", "settimeout"):
        assert token not in LOW, f"forbidden JS timer token present: {token!r}"


# ── UX-DR10: self-contained, no external assets ──


def test_no_external_protocols_or_assets():
    # No scheme-qualified or protocol-relative URLs anywhere.
    assert "://" not in HTML, "no http(s):// (or any scheme) — page must be self-contained"
    assert '="//' not in HTML, "no protocol-relative // URL"
    # No external stylesheet / script / import / remote url().
    assert "<link" not in LOW, "no external <link> stylesheet"
    assert "src=" not in LOW, "no <script src>/<img src> external asset"
    assert "@import" not in LOW, "no CSS @import"
    assert "url(" not in LOW, "no url(...) (would allow a remote asset)"


def test_uses_system_font_stack_only():
    # System/inline font stack; never a web font or @font-face.
    assert "system-ui" in LOW
    assert "@font-face" not in LOW


def test_all_css_is_inline():
    # Exactly one inline <style>; styling is not pulled from an external sheet.
    assert "<style>" in LOW and "</style>" in LOW
