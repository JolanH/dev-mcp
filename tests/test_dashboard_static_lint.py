"""Static CSS/JS lint over the rendered board (UX-DR4 + UX-DR10 + FR-10 read-only).

Pure string grep over ``render_board(...)`` output and the inlined poller source
(no parser, no dep):
* **No motion (UX-DR4):** absent ``transition`` / ``animation`` / ``@keyframes`` /
  ``scroll-behavior:smooth`` in CSS; no frame-timer redraw callbacks and no repeating
  interval timer in JS. ``setTimeout`` IS permitted from Story 2.4b — it re-arms the
  ``/state`` *data* poll (a fetch, not a style mutation), which UX-DR4 explicitly allows.
* **Self-contained (UX-DR10):** all CSS/JS inline — the poller is INLINED, never an
  external ``<script src>``; no ``http(s)://``, no protocol-relative ``//``, no
  ``<link … href>``, no ``@import``, no ``url(http…)``; system font stack only.
* **Read-only (FR-10):** the poller only ``fetch("/state")`` with GET — no POST/PUT/
  DELETE/PATCH fetch, no ``XMLHttpRequest``/``sendBeacon``; the page exposes no
  create/edit/remove/launch control (no ``<form>``/``<button>``/``<input>``, no MCP
  mutating tool name).
"""

from __future__ import annotations

import re

from dev_helper_mcp.dashboard.render import _POLLER_JS, render_board


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


def test_no_frame_timer_or_interval_style_mutation():
    # UX-DR4 forbids frame-timer redraw callbacks and a repeating interval timer
    # (both are vectors for timer-driven *style* mutation / overlapping polls).
    for token in ("requestanimationframe", "setinterval"):
        assert token not in LOW, f"forbidden JS timer token present: {token!r}"


def test_settimeout_is_present_and_is_the_poll_rearm():
    # Story 2.4b: the page now carries the inlined poller, which re-arms its /state
    # poll with setTimeout AFTER each poll resolves (data fetch, not style mutation —
    # UX-DR4 permits it). Its presence is expected, not forbidden.
    assert "settimeout" in LOW, "the 2.4b poller must re-arm via setTimeout"


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


# ── Story 2.4b: the inlined poller is GET-only (FR-10) + self-contained (UX-DR10) ──

JS = _POLLER_JS
JS_LOW = JS.lower()

# Page markup with all <script>…</script> blocks removed. Lint checks for external
# assets / interactive controls run against THIS (not raw HTML) so they cannot
# false-match on the inlined poller JS or the embedded initial-state JSON — e.g. a
# task slug like "fix-the-input-bug" or a JS token like `el.src` must not trip them.
MARKUP = re.sub(r"<script\b[^>]*>.*?</script>", "", HTML, flags=re.IGNORECASE | re.DOTALL)
MARKUP_LOW = MARKUP.lower()


def test_poller_is_inlined_not_an_external_asset():
    # The poller's source is embedded verbatim in the page (UX-DR10), not referenced
    # via an external <script src>. A distinctive token from the source must appear
    # inline, and no <script> element may carry a src attribute.
    assert "function diff(" in JS, "sanity: poller source has the diff() function"
    assert "function diff(" in HTML, "the poller must be INLINED into the page"
    # Anchor to an actual <script ... src ...> element rather than the bare "src="
    # substring (which would false-match a data value or a JS token like `el.src`).
    assert re.search(r"<script[^>]*\bsrc\b", LOW) is None, "no external <script src>"


def test_poller_fetches_only_get_state():
    # The single network call is GET /state. No mutating HTTP method anywhere, in any
    # case / quote style / spacing, and no alternative request transport.
    assert 'fetch("/state"' in JS, "the poller fetches /state"
    assert JS.count("fetch(") == 1, "the poller makes exactly one fetch call"
    # Match a `method:` value so the check can't collide with the DOM-only function
    # named `patch`; case-insensitive, any quote, any spacing.
    mutating = re.search(r"""method\s*:\s*['"`](post|put|delete|patch)['"`]""", JS, re.IGNORECASE)
    assert mutating is None, (
        f"poller must issue only GET fetches; found {mutating.group(0)!r}" if mutating else ""
    )
    assert "xmlhttprequest" not in JS_LOW, "no XMLHttpRequest"
    assert "sendbeacon" not in JS_LOW, "no navigator.sendBeacon"


def test_poller_never_calls_a_mutating_mcp_tool():
    # The poller is a read-only board; it must never invoke a mutating MCP tool.
    for tool in ("create_task", "update_task", "remove_worktree"):
        assert tool not in JS, f"poller must not call the mutating tool {tool!r}"


def test_poller_source_has_no_motion_or_external_asset():
    # The same UX-DR4/DR10 guards applied to the JS source directly (belt + braces).
    for token in (
        "requestanimationframe",
        "setinterval",
        "transition",
        "animation",
        "@keyframes",
    ):
        assert token not in JS_LOW, f"forbidden token in poller source: {token!r}"
    assert "://" not in JS, "no scheme-qualified URL in the poller (egress-free)"
    assert "url(" not in JS_LOW, "no url(...) in the poller"


def test_page_exposes_no_mutating_control():
    # FR-10: a read-only board — no form, no button, no text input, no select that
    # could create/modify/remove a task/worktree or launch an agent. Checked against
    # MARKUP (scripts stripped) so a control token appearing inside the inlined JS or
    # the embedded JSON (e.g. a task description) can't trip a false failure.
    for control in ("<form", "<button", "<input", "<textarea", "<select"):
        assert control not in MARKUP_LOW, f"read-only board must not contain {control!r}"


def test_initial_state_and_poll_interval_are_embedded():
    # The poller seeds `prev` from the embedded initial /state JSON and reads the
    # cadence off the .pg root — both must be present so the first poll diffs against
    # the server-rendered state, not an empty board.
    assert 'id="initial-state"' in LOW, "initial /state snapshot must be embedded"
    assert "data-poll-interval=" in LOW, "poll interval must be injected onto the page"


# ── Story 2.4c: freshness/degrade is data, not motion (UX-DR4/6/10) ──


def test_freshness_threshold_injected_not_hardcoded_in_js():
    # The client reads the stale threshold off the page (data-stale-threshold-ms), it is
    # not a magic number in the JS — mirrors the no-hardcoded-cadence rule for 2.4b.
    assert "data-stale-threshold-ms=" in LOW, "the stale threshold must be injected onto the page"
    assert "3000" not in JS, "the stale threshold must not be hardcoded in the poller"


def test_stale_treatment_is_a_class_swap_not_a_css_transition():
    # The grey→amber stale shift is a `.fresh.stale` CLASS rule, NOT a CSS transition/
    # animation (those are already globally forbidden by test_no_motion_tokens). Assert the
    # class hook exists so the shift is a state swap.
    assert ".fresh.stale{" in HTML, "the stale shift must be a class swap (.fresh.stale)"


def test_freshness_uses_no_new_timer_only_the_existing_poll_rearm():
    # Story 2.4c keeps the age counting on the existing setTimeout poll re-arm — it adds NO
    # second timer. setInterval/requestAnimationFrame stay forbidden (test_no_motion_tokens
    # already asserts their absence in LOW); here, belt-and-braces over the JS source, and
    # confirm the page still carries exactly the one poll fetch.
    assert "setinterval" not in JS_LOW, "2.4c must not introduce a stamp-ticking setInterval"
    assert JS.count("fetch(") == 1, "still exactly one /state poll fetch (no new transport)"
