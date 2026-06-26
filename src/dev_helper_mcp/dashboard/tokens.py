"""Dashboard design tokens — the single source of truth (Story 2.4a, Decision B).

The DESIGN.md frontmatter ``colors:`` block is the authority; the hex values here
are copied **verbatim** from it. The renderer (``render.py``) injects these into the
served ``:root{…}`` CSS, and the contrast test (``test_dashboard_contrast.py``)
imports the SAME constants — so the tested ratios are over the *served* colors,
impossible to drift (Decision B).

Pure module — no ``mcp``/``starlette`` import, no I/O. It lives under the adapter
``dashboard/`` package (so it is not in ``test_adapter_seam.py``'s SEAM_MODULES),
but carries no SDK dependency of its own.

[Source: DESIGN.md frontmatter colors:; project-context.md "tokens in one place".]
"""

from __future__ import annotations

# ── Surfaces ──
BG = "#0e1117"  # page background
SURFACE = "#161b22"  # card / pill surface
BORDER = "#21262d"  # hairline border
BAR_DONE = "#373e47"  # dimmer left bar for the (decorative) done card

# ── Text ──
TEXT = "#e6edf3"  # primary text
TEXT_MUTED = "#8b949e"  # secondary text (column headers, worktree branch)
TEXT_DIM = "#586069"  # tertiary text / freshness stamp
WORKTREE_REPO = "#c9d1d9"  # repo name in a worktree line

# ── Status palette (the load-bearing colors) ──
RUNNING = "#39d0a8"
RUNNING_BORDER = "#1f3f37"
BLOCKED = "#e3a34a"
BLOCKED_BG = "#2a2113"
BLOCKED_BORDER = "#3d3320"
REVIEW = "#6cb6ff"
REVIEW_BG = "#16263d"
REVIEW_BORDER = "#1f3a5c"
DONE = "#7d8590"

#: Per-card glyph per status — the shape-distinct, non-color accessibility channel
#: (● running · ▲ blocked · ◆ review · ✓ done). Travels on every card, not just the
#: column header (DESIGN.md:115; EXPERIENCE.md:81).
STATUS_GLYPH: dict[str, str] = {
    "running": "●",
    "blocked": "▲",
    "review": "◆",
    "done": "✓",
}

#: The three ACTIVE columns, in lifecycle order (Running | Blocked | Review). ``done``
#: is deliberately absent — it is a folded disclosure, never a column (UX-DR13).
ACTIVE_COLUMNS: tuple[str, ...] = ("running", "blocked", "review")

#: Per-status colors used for the card left bar / glyph / column header / pill. ``done``
#: maps to the grey ``DONE`` text/glyph color; the done *bar* uses the dimmer
#: ``BAR_DONE`` (decorative — see render.py / the contrast test rationale).
STATUS_COLOR: dict[str, str] = {
    "running": RUNNING,
    "blocked": BLOCKED,
    "review": REVIEW,
    "done": DONE,
}

#: Short CSS class suffix per status (`.card.run/.blk/.rev/.done`, matching the mock).
STATUS_CLASS: dict[str, str] = {
    "running": "run",
    "blocked": "blk",
    "review": "rev",
    "done": "done",
}

#: Reason badge copy per status (only blocked/review carry one). NEVER the word
#: "merge" — review means "needs your eyes", not "ready to merge" (DESIGN.md:118-119).
STATUS_BADGE: dict[str, str] = {
    "blocked": "needs input",
    "review": "awaiting review",
}

#: Human column label per status. Title-case throughout (the summary pill lowercases
#: it for "0 done"); kept consistent so any future reader of STATUS_LABEL["done"] gets
#: the same casing as the others.
STATUS_LABEL: dict[str, str] = {
    "running": "Running",
    "blocked": "Blocked",
    "review": "Review",
    "done": "Done",
}

# ── Internal consistency guard (fail fast at import) ──
# render.py's "total, never raises" guarantee leans on these maps agreeing on their
# status keys: a divergent edit (a status present in one map but missing from another,
# or an ACTIVE_COLUMNS/STATUS_BADGE entry with no backing color/glyph) would otherwise
# surface as a blank a11y glyph or a KeyError deep inside the renderer. Pin it here so a
# bad token edit fails loudly at import, not silently in a served page.
_STATUS_KEYS = set(STATUS_GLYPH)
assert set(STATUS_COLOR) == _STATUS_KEYS, "STATUS_COLOR keys must match STATUS_GLYPH"
assert set(STATUS_CLASS) == _STATUS_KEYS, "STATUS_CLASS keys must match STATUS_GLYPH"
assert set(STATUS_LABEL) == _STATUS_KEYS, "STATUS_LABEL keys must match STATUS_GLYPH"
assert set(STATUS_BADGE) <= _STATUS_KEYS, "STATUS_BADGE keys must be a subset of the statuses"
assert set(ACTIVE_COLUMNS) <= _STATUS_KEYS, "ACTIVE_COLUMNS must be a subset of the statuses"
