"""Pure server-side board renderer (Story 2.4a, Decision A).

``render_board(snapshot: dict) -> str`` turns the snake_case ``/state`` payload
(the ``dataclasses.asdict(CacheSnapshot)`` shape served by Story 2.3) into a
**complete, self-contained** HTML page string. It is:

* **Pure & total** — takes a plain dict, returns a string; no ``mcp``/``starlette``
  import, no I/O, no clock read; never raises on an orphaned/warning-bearing or
  partial payload (mirrors Story 2.1's projection purity, so it is unit-testable
  with no server). Accepts the *dict* shape (not the dataclass) so tests build
  payloads by hand.
* **Self-contained** — all CSS inline, system font stack only, no external asset,
  no script, no network egress (UX-DR10). The page is fully formed by the server
  and correct with JavaScript disabled (the live poll loop is Story 2.4b).
* **Accessible & static** — status is encoded by column + colored left bar +
  per-card glyph (●/▲/◆/✓) + a ``data-status`` attribute, never color alone
  (UX-DR3); nothing animates (UX-DR4).

Scope fence (2.4a): renders the happy-path board for a fixed payload. The JS
poller/diff-and-patch is **2.4b**; freshness/stale/degraded/orphan/empty-state
copy is **2.4c**. This story OWNS the card markup contract (classes, ``data-status``,
``data-task-id``, glyph, ``.wt`` lines) that 2.4b patches.

[Source: DESIGN.md; EXPERIENCE.md; mockups/key-screen-board.html; Story 2.4a tasks.]
"""

from __future__ import annotations

from collections import Counter
from html import escape

from . import tokens

# ── Inline stylesheet ───────────────────────────────────────────────────────
# Adapted from the reference mock (mockups/key-screen-board.html:2-62): the mock's
# class system reproduced verbatim, with the two 2.4a ADDITIONS the mock omits —
# the UX-DR12 overflow contract (.cols overflow-x hidden, .col overflow-y auto +
# max-height, a 3->1 wrap media query). Tokens are interpolated from tokens.py so
# the served colors ARE the contrast-tested colors (Decision B). No transition /
# animation / @keyframes / scroll-behavior:smooth anywhere (UX-DR4); system font
# stack only, no @font-face / external asset (UX-DR10).
_STYLE = f"""
  :root{{ color-scheme:dark;
    --bg:{tokens.BG}; --card:{tokens.SURFACE}; --border:{tokens.BORDER};
    --bar-done:{tokens.BAR_DONE};
    --txt:{tokens.TEXT}; --mut:{tokens.TEXT_MUTED}; --dim:{tokens.TEXT_DIM};
    --repo:{tokens.WORKTREE_REPO};
    --run:{tokens.RUNNING}; --run-bd:{tokens.RUNNING_BORDER};
    --blk:{tokens.BLOCKED}; --blk-bg:{tokens.BLOCKED_BG}; --blk-bd:{tokens.BLOCKED_BORDER};
    --rev:{tokens.REVIEW}; --rev-bg:{tokens.REVIEW_BG}; --rev-bd:{tokens.REVIEW_BORDER};
    --done:{tokens.DONE};
    --sans:ui-sans-serif,system-ui,sans-serif;
    --mono:ui-monospace,"SF Mono",Menlo,monospace;
  }}
  *{{ box-sizing:border-box; }}
  body{{ margin:0; }}
  .pg{{ background:var(--bg); color:var(--txt); font-family:var(--sans);
       padding:16px 18px 26px; max-width:1000px; margin:0 auto; min-height:100vh; }}

  /* top summary bar */
  .summary{{ display:flex; align-items:center; gap:8px; margin-bottom:16px; flex-wrap:wrap;}}
  .pill{{ display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600;
         padding:4px 11px; border-radius:999px; border:1px solid var(--border); background:var(--card);}}
  .pill .g{{ font-size:10px; line-height:1;}}
  .pill.run{{ color:var(--run); border-color:var(--run-bd);}}
  .pill.blk{{ color:var(--blk); border-color:var(--blk-bd); font-weight:700;}}
  .pill.rev{{ color:var(--rev); border-color:var(--rev-bd);}}
  .pill.done{{ color:var(--done);}}
  .fresh{{ margin-left:auto; font-size:11.5px; color:var(--dim);}}

  /* three active columns + UX-DR12 overflow contract (ADDED by 2.4a) */
  .cols{{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; align-items:start;
         overflow-x:hidden;}}
  .col{{ overflow-y:auto; max-height:calc(100vh - 96px);}}
  .col-h{{ font-size:11.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
          color:var(--mut); margin:0 2px 9px; display:flex; align-items:center; gap:6px;}}
  .col-h .n{{ color:var(--dim); font-weight:600;}}
  .col-run .col-h{{ color:var(--run);}} .col-blk .col-h{{ color:var(--blk);}} .col-rev .col-h{{ color:var(--rev);}}

  /* cards — compact, status-colored left bar, per-card glyph */
  .card{{ position:relative; overflow:hidden; background:var(--card); border:1px solid var(--border);
         border-radius:8px; padding:9px 11px 9px 14px; margin-bottom:8px; box-shadow:0 1px 2px rgba(0,0,0,.3);}}
  .card::before{{ content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--bar-done);}}
  .card.run::before{{ background:var(--run);}} .card.blk::before{{ background:var(--blk);}} .card.rev::before{{ background:var(--rev);}}
  /* EMPHASIS: blocked is the alarm — lifted + glow; running/review are flat baselines */
  .card.blk{{ box-shadow:0 0 0 1px var(--blk-bd), 0 2px 12px rgba(227,163,74,.14); border-color:var(--blk-bd);}}
  .t{{ display:flex; align-items:center; gap:7px; font-size:13px; font-weight:650; color:var(--txt);}}
  .t .g{{ font-size:11px; line-height:1; flex:none;}}      /* per-card glyph = non-color channel */
  .run .t .g{{ color:var(--run);}} .blk .t .g{{ color:var(--blk);}} .rev .t .g{{ color:var(--rev);}} .done .t .g{{ color:var(--done);}}
  .badge{{ margin-left:auto; font-size:10.5px; font-weight:600; padding:2px 7px; border-radius:5px;}}
  .badge.b{{ color:var(--blk); background:var(--blk-bg);}}
  .badge.r{{ color:var(--rev); background:var(--rev-bg);}}
  .wt{{ font-family:var(--mono); font-size:11.5px; color:var(--mut); line-height:1.55; padding:4px 0 0 14px;}}
  .wt .repo{{ color:var(--repo);}}

  /* folded done-disclosure below the board */
  .fold{{ margin-top:14px; border-top:1px solid var(--border); padding-top:11px;}}
  .fold summary{{ font-size:11.5px; color:var(--dim); cursor:pointer; list-style:none;}}
  .fold summary::before{{ content:"\\25b8 "; }} .fold[open] summary::before{{ content:"\\25be "; }}
  .fold.done summary{{ color:var(--done);}}
  .fold .body{{ margin-top:9px;}}
  .donecard{{ opacity:.55; padding-left:16px; font-size:12px; color:var(--mut); line-height:1.7;}}
  .donecard .g{{ color:var(--done); font-size:11px;}} .donecard b{{ color:var(--repo); font-weight:600;}}

  /* 3->1 wrap at a narrow width — static layout, not motion (UX-DR4-safe, UX-DR12) */
  @media (max-width:680px){{ .cols{{ grid-template-columns:1fr; }} }}
"""


def _repo_basename(repo_path: str) -> str:
    """Last path segment of ``repo_path`` (display name); the full path goes on the
    worktree line's ``title`` hover. Pure string arithmetic, no filesystem touch."""
    trimmed = repo_path.rstrip("/")
    return trimmed.rsplit("/", 1)[-1] or repo_path


def _worktree_line(wt: dict) -> str:
    """One ``repo · branch`` monospace line; full worktree path (or repo path) on hover."""
    repo = _repo_basename(str(wt.get("repo_path", "")))
    branch = str(wt.get("branch", ""))
    # Prefer the concrete worktree path for the tooltip; fall back to the repo path
    # (an orphaned link has path=None — be total, never crash). 2.4c styles orphans.
    title = wt.get("path") or wt.get("repo_path") or ""
    return (
        f'<div class="wt" title="{escape(str(title), quote=True)}">'
        f'<span class="repo">{escape(repo)}</span> · {escape(branch)}</div>'
    )


def _card(task: dict) -> str:
    """An active task-card: status-class + ``data-status``/``data-task-id`` hooks +
    per-card glyph + optional reason badge + one ``.wt`` line per repo (grouped by
    task — ONE card with N worktree lines, never N cards)."""
    status = task.get("status") or ""
    cls = tokens.STATUS_CLASS.get(status, "")
    glyph = tokens.STATUS_GLYPH.get(status, "")
    task_id = str(task.get("task_id", ""))

    badge_text = tokens.STATUS_BADGE.get(status)
    if status == "blocked":
        badge = f'<span class="badge b">{escape(badge_text)}</span>'
    elif status == "review":
        badge = f'<span class="badge r">{escape(badge_text)}</span>'
    else:
        badge = ""

    worktrees = task.get("worktrees") or ()
    wt_html = "".join(_worktree_line(wt) for wt in worktrees)

    return (
        f'<div class="card {cls}" data-status="{escape(status, quote=True)}" '
        f'data-task-id="{escape(task_id, quote=True)}">'
        f'<div class="t"><span class="g">{glyph}</span>{escape(task_id)}{badge}</div>'
        f"{wt_html}</div>"
    )


def _column(status: str, tasks: list[dict]) -> str:
    """One active column: ``col-h`` header (glyph + label + count) over its cards."""
    cls = tokens.STATUS_CLASS[status]
    glyph = tokens.STATUS_GLYPH[status]
    label = tokens.STATUS_LABEL[status]
    cards = [t for t in tasks if (t.get("status") or "") == status]
    cards_html = "".join(_card(t) for t in cards)
    return (
        f'<div class="col col-{cls}">'
        f'<div class="col-h">{glyph} {escape(label)} <span class="n">{len(cards)}</span></div>'
        f"{cards_html}</div>"
    )


def _donecard(task: dict) -> str:
    """A folded done entry: ✓ glyph + task id + its ``repo · branch`` worktrees. Carries
    ``data-status="done"``/``data-task-id`` for the 2.4b diff-and-patch contract."""
    task_id = str(task.get("task_id", ""))
    glyph = tokens.STATUS_GLYPH["done"]
    worktrees = task.get("worktrees") or ()
    wt_html = "".join(_worktree_line(wt) for wt in worktrees)
    return (
        f'<div class="donecard done" data-status="done" '
        f'data-task-id="{escape(task_id, quote=True)}">'
        f'<span class="g">{glyph}</span> <b>{escape(task_id)}</b>{wt_html}</div>'
    )


def _summary_pill(status: str, count: int) -> str:
    """One summary-bar pill ``<glyph> <count> <label>``; zero-counts still render so
    absence is legible (UX-DR2). The blocked pill is bolder (via the ``.pill.blk`` rule)."""
    cls = tokens.STATUS_CLASS[status]
    glyph = tokens.STATUS_GLYPH[status]
    label = tokens.STATUS_LABEL[status].lower()
    return f'<span class="pill {cls}"><span class="g">{glyph}</span>{count} {escape(label)}</span>'


def render_board(snapshot: dict) -> str:
    """Render the full board HTML page for a ``/state`` snapshot dict.

    Pure and total: never reads a clock, never raises on a partial/orphan/warning
    payload. Tasks with an unknown/absent status (e.g. an untracked crash-orphan
    slug, ``status=None``) are not placed in any column or the done-disclosure here
    — their dedicated rendering is Story 2.4c; 2.4a stays total by skipping them.
    """
    tasks: list[dict] = list(snapshot.get("tasks") or ())
    generated_at = str(snapshot.get("generated_at", ""))

    # Counts grouped by status (only the four known statuses participate in pills/
    # columns/disclosure; unknown-status tasks are skipped — 2.4c renders those).
    counts: Counter[str] = Counter(
        t.get("status") for t in tasks if t.get("status") in tokens.STATUS_GLYPH
    )

    # Summary bar: one pill per status in fixed order (incl. done) + freshness stamp.
    pills = "".join(
        _summary_pill(s, counts.get(s, 0)) for s in ("running", "blocked", "review", "done")
    )
    fresh = (
        f'<span class="fresh" data-generated-at="{escape(generated_at, quote=True)}">'
        f"{escape(generated_at)}</span>"
    )
    summary = f'<div class="summary">{pills}{fresh}</div>'

    # Board: exactly three active columns in lifecycle order (done is NOT a column).
    cols = "".join(_column(s, tasks) for s in tokens.ACTIVE_COLUMNS)
    board = f'<div class="cols">{cols}</div>'

    # Done-disclosure: collapsed (no `open`), summary "✓ N done", dimmed donecards.
    done_tasks = [t for t in tasks if (t.get("status") or "") == "done"]
    done_glyph = tokens.STATUS_GLYPH["done"]
    done_body = "".join(_donecard(t) for t in done_tasks)
    done_fold = (
        f'<details class="fold done"><summary>{done_glyph} {len(done_tasks)} done</summary>'
        f'<div class="body">{done_body}</div></details>'
    )

    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>dev-helper-mcp</title>"
        f"<style>{_STYLE}</style></head>"
        f'<body><div class="pg">{summary}{board}{done_fold}</div></body></html>'
    )
