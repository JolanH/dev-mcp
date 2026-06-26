"""Story 2.4a — WCAG-AA contrast over the enumerated token pairs (UX-DR11).

A pure ``contrast_ratio(hex_fg, hex_bg) -> float`` (WCAG 2.x relative luminance, no
dep) imported against ``dashboard/tokens.py`` — so the tested ratios are over the
*served* colors (Decision B; impossible to drift). UX-DR11's rule: no adjective
without a threshold — text ≥ 4.5:1, non-text (bar/glyph) ≥ 3:1, against both
``{bg}`` and ``{surface}``.

⚠️ The one borderline pair — RESOLVED, NOT silently downgraded
--------------------------------------------------------------
EXPERIENCE.md:82 enumerates "each status text + bar against bg and surface". Every
status **text** clears 4.5:1 (the tight one is ``done`` on ``surface`` ≈ 4.64:1 —
above AA; the story's ~4.3 estimate was conservative) and every status **glyph**
clears 3:1. The active-status **bars** (running/blocked/review use the status color)
clear 3:1 comfortably. The single token that does NOT meet 3:1 is the ``done`` card's
left **bar** (``BAR_DONE`` ≈ 1.6:1 vs surface) — but that bar is **decorative, not a
status channel**: ``done`` is segregated in its own ``<details>`` disclosure (position),
tagged with the ``✓`` glyph (which DOES clear 3:1) and dimmed; no other status shares
that disclosure for the bar to disambiguate. So the done bar is asserted as decorative
(the dimmer ``BAR_DONE`` is intentional per DESIGN.md:78), and the ``done`` non-color
channel under the 3:1 floor is the ``✓`` glyph, which is asserted explicitly below.
(The 4.5 text threshold is never weakened.) Resolution recorded in the Change Log.
"""

from __future__ import annotations

from dev_helper_mcp.dashboard import tokens

TEXT_AA = 4.5  # WCAG AA for normal text
NONTEXT_AA = 3.0  # WCAG AA for non-text (graphical objects: bar, glyph)


def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two ``#rrggbb`` colors (order-independent)."""
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# Sanity-anchor the math against a known pair (black/white == 21:1).
def test_contrast_ratio_math_is_correct():
    assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast_ratio("#ffffff", "#ffffff"), 1) == 1.0


def test_primary_text_meets_aa_on_both_surfaces():
    for surface in (tokens.BG, tokens.SURFACE):
        assert contrast_ratio(tokens.TEXT, surface) >= TEXT_AA


def test_every_status_text_meets_aa_on_both_surfaces():
    # running/blocked/review/done text color (== STATUS_COLOR) must clear 4.5:1 on bg
    # AND surface. The 4.5 threshold is NEVER lowered (done is the tight one ≈4.64).
    for status, color in tokens.STATUS_COLOR.items():
        for surface_name, surface in (("bg", tokens.BG), ("surface", tokens.SURFACE)):
            ratio = contrast_ratio(color, surface)
            assert ratio >= TEXT_AA, (
                f"{status} text {color} on {surface_name} is {ratio:.2f}:1 (< {TEXT_AA})"
            )


def test_every_status_glyph_meets_nontext_aa_on_both_surfaces():
    # The per-card glyph (●/▲/◆/✓) is the non-color channel — must clear 3:1 (incl. the
    # done ✓, the channel that carries done under the floor). Glyph uses STATUS_COLOR.
    for status, color in tokens.STATUS_COLOR.items():
        for surface in (tokens.BG, tokens.SURFACE):
            assert contrast_ratio(color, surface) >= NONTEXT_AA, f"{status} glyph fails 3:1"


def test_active_status_bars_meet_nontext_aa():
    # Active-column left bars use the status color → must clear 3:1 as a graphical object.
    for status in tokens.ACTIVE_COLUMNS:
        color = tokens.STATUS_COLOR[status]
        for surface in (tokens.BG, tokens.SURFACE):
            assert contrast_ratio(color, surface) >= NONTEXT_AA, f"{status} bar fails 3:1"


def test_done_bar_is_decorative_not_a_status_channel():
    # The done card's left bar uses the dimmer BAR_DONE (DESIGN.md:78) and does NOT meet
    # 3:1 — this is intentional and acceptable ONLY because the bar is decorative there:
    # done is segregated in its disclosure + carries the ✓ glyph (asserted ≥3:1 above) +
    # is dimmed. We pin the decision so a future change can't silently rely on the done
    # bar for status. (If the done bar ever becomes load-bearing, this test must change.)
    assert contrast_ratio(tokens.BAR_DONE, tokens.SURFACE) < NONTEXT_AA
    # The real non-color channel for done — its glyph — clears the non-text floor.
    assert contrast_ratio(tokens.STATUS_COLOR["done"], tokens.SURFACE) >= NONTEXT_AA
