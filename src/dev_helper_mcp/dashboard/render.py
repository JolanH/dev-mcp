"""Pure server-side board renderer (Story 2.4a, Decision A).

``render_board(snapshot: dict) -> str`` turns the snake_case ``/state`` payload
(the ``dataclasses.asdict(CacheSnapshot)`` shape served by Story 2.3) into a
**complete, self-contained** HTML page string. It is:

* **Pure & total** — takes a plain dict, returns a string; no ``mcp``/``starlette``
  import, no I/O, no clock read; never raises on an orphaned/warning-bearing or
  partial payload (mirrors Story 2.1's projection purity, so it is unit-testable
  with no server). Accepts the *dict* shape (not the dataclass) so tests build
  payloads by hand.
* **Self-contained** — all CSS inline, the poller JS **inlined** (no external
  ``<script src>``), system font stack only, no network egress beyond the poller's
  own ``fetch("/state")`` on localhost (UX-DR10). The page is fully formed by the
  server and correct with JavaScript disabled; the live poll loop (Story 2.4b) only
  keeps an already-correct board current.
* **Accessible & static** — status is encoded by column + colored left bar +
  per-card glyph (●/▲/◆/✓) + a ``data-status`` attribute, never color alone
  (UX-DR3); nothing animates (UX-DR4).

Scope fence: this module OWNS the card markup contract (classes, ``data-status``,
``data-task-id``, glyph, ``.wt`` lines) that the 2.4b poller patches — the JS
``renderCard`` in ``static/poller.js`` MIRRORS it (Decision B). Story 2.4b extends
``render_board`` minimally to (a) inject ``DASHBOARD_POLL_INTERVAL_MS`` as a
``data-poll-interval`` attribute, (b) embed the initial ``/state`` snapshot as JSON
the poller seeds from, and (c) inline ``static/poller.js`` — without changing the
markup contract. freshness/stale/degraded/orphan/empty-state copy is **2.4c**.

[Source: DESIGN.md; EXPERIENCE.md; mockups/key-screen-board.html; Story 2.4a tasks.]
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from html import escape
from importlib import resources

from .. import config
from . import tokens

#: Stale threshold (UX-DR6: "older than 2× the poll interval"). Derived from the two
#: tunables so the server-injected ``data-stale-threshold-ms`` and the server-rendered
#: stale-at-load class (Decision B) use the SAME number the client poller reads.
_STALE_THRESHOLD_MS = config.DASHBOARD_POLL_INTERVAL_MS * config.DASHBOARD_STALE_FACTOR

# The poller JS is read ONCE here at import (not inside ``render_board``) so the
# renderer itself stays pure & I/O-free — it only interpolates this module constant.
# It is a packaged static asset under ``dashboard/static/`` (shipped in the wheel by
# uv_build); ``importlib.resources`` resolves it whether running from source or an
# installed package. It is INLINED into the served page (UX-DR10: no external
# ``<script src>``); the file stays a real source file so ``node --test`` can import
# and unit-test its pure ``diff()`` (AR-12).
_POLLER_JS = (
    resources.files("dev_helper_mcp.dashboard").joinpath("static/poller.js").read_text("utf-8")
)

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
  /* stale freshness = grey->amber CLASS SWAP (UX-DR6; not motion). Reuses the blocked
     amber so no fifth color is introduced; the marker spells out the degrade (UX-DR8). */
  .fresh.stale{{ color:var(--blk);}}
  .fresh .stale-marker{{ color:var(--blk); font-weight:600;}}

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
  /* per-repo degrade (UX-DR8): a repo_unavailable line dims + a muted "· unavailable"
     marker; only that repo's lines, never the whole board. */
  .wt[data-unavailable] .repo{{ color:var(--dim);}}
  .wt .un{{ color:var(--dim); font-style:italic;}}

  /* folded done-disclosure below the board */
  .fold{{ margin-top:14px; border-top:1px solid var(--border); padding-top:11px;}}
  .fold summary{{ font-size:11.5px; color:var(--dim); cursor:pointer; list-style:none;}}
  .fold summary::before{{ content:"\\25b8 "; }} .fold[open] summary::before{{ content:"\\25be "; }}
  .fold.done summary{{ color:var(--done);}}
  .fold .body{{ margin-top:9px;}}
  .donecard{{ opacity:.55; padding-left:16px; font-size:12px; color:var(--mut); line-height:1.7;}}
  .donecard .g{{ color:var(--done); font-size:11px;}} .donecard b{{ color:var(--repo); font-weight:600;}}
  /* orphan-disclosure body line (UX-DR7): self-explaining, demoted, lowercase fragment */
  .fold .o{{ font-size:12px; color:var(--mut); line-height:1.7;}}
  .fold .o b{{ color:var(--repo); font-weight:600;}}

  /* empty states (UX-DR9): quiet, informative, not decorative */
  .empty{{ font-size:12px; color:var(--dim); padding:4px 2px;}}
  .empty-board{{ margin-top:14px; font-size:13px; color:var(--mut);}}
  .empty-board code{{ font-family:var(--mono); color:var(--repo);}}
  [hidden]{{ display:none;}}

  /* 3->1 wrap at a narrow width — static layout, not motion (UX-DR4-safe, UX-DR12) */
  @media (max-width:680px){{ .cols{{ grid-template-columns:1fr; }} }}
"""


def _repo_basename(repo_path: str) -> str:
    """Last path segment of ``repo_path`` (display name); the full path goes on the
    worktree line's ``title`` hover. Pure string arithmetic, no filesystem touch."""
    trimmed = repo_path.rstrip("/")
    return trimmed.rsplit("/", 1)[-1] or repo_path


def _warnings_index(warnings: list) -> tuple[set[str], list[dict]]:
    """Split the ``/state`` ``warnings`` list into the TWO distinct degrade buckets
    (Story 2.4c / UX-DR8) — the headline gotcha: they look alike but mean opposites.

    * ``repo_unavailable:<repo_path>`` (2.2 Decision B) — a transient/slow read this
      tick; its worktrees are carry-forward last-known → that repo's lines render
      "unavailable" (it recovers). → an entry in the returned ``unavailable_repos`` set.
    * ``orphan_link:<task_id>@<repo>:<branch>`` (2.1) — a branch GENUINELY gone from git
      (note preserved) → the orphan disclosure. → a ``{task_id, repo, branch}`` dict.

    Pure & total (mirrors the JS ``warningsIndex``); an unknown warning kind is ignored.
    """
    unavailable: set[str] = set()
    orphans: list[dict] = []
    for raw in warnings or ():
        w = str(raw)
        if w.startswith("repo_unavailable:"):
            unavailable.add(w[len("repo_unavailable:") :])
        elif w.startswith("orphan_link:"):
            rest = w[len("orphan_link:") :]
            task_id, _, repo_branch = rest.partition("@")
            # branch is ``agent/<slug>`` (no colon); a repo PATH uses ``/``, so split on
            # the LAST ``:`` to separate <repo> from <branch> robustly.
            repo, _, branch = repo_branch.rpartition(":")
            orphans.append({"task_id": task_id, "repo": repo, "branch": branch})
    return unavailable, orphans


def _parse_iso_ms(generated_at: str) -> int | None:
    """Epoch milliseconds for a ``now_iso()`` UTC-``Z`` stamp, or ``None`` if unparseable.

    Pure: parses a GIVEN string — never reads a clock (purity is preserved; the only
    clock in the freshness path is the injected ``now_ms``)."""
    try:
        return int(datetime.fromisoformat(generated_at).timestamp() * 1000)
    except ValueError, TypeError:
        return None


def _staleness(generated_at: str, now_ms: int, threshold_ms: int) -> tuple[bool, str]:
    """Server mirror of the JS ``staleness`` (Decision B — same predicate as the client).

    ``now_ms`` is INJECTED for determinism (like 2.1's injected ``generated_at``); the
    renderer stays pure. Stale when age STRICTLY EXCEEDS the threshold (UX-DR6, exclusive).
    """
    parsed = _parse_iso_ms(generated_at)
    if parsed is None:
        return (False, "")
    age = max(0, now_ms - parsed)
    return (age > threshold_ms, f"updated {age // 1000}s ago")


def _worktree_line(wt: dict, unavailable_repos: set[str] = frozenset()) -> str:
    """One ``repo · branch`` monospace line; full worktree path (or repo path) on hover.

    Carries ``data-repo`` so the 2.4c poller can re-apply the per-repo degrade by repo
    without re-parsing the basename (mirrors the JS ``buildWorktreeLine``). When the repo
    is ``repo_unavailable:`` this tick it is marked ``data-unavailable`` + a muted
    "· unavailable" marker (last-known, never blank — UX-DR8); other repos render normally.
    """
    repo_path = str(wt.get("repo_path", ""))
    repo = _repo_basename(repo_path)
    branch = str(wt.get("branch", ""))
    # Prefer the concrete worktree path for the tooltip; fall back to the repo path
    # (an orphaned link has path=None — be total, never crash).
    title = wt.get("path") or wt.get("repo_path") or ""
    attrs = f' data-repo="{escape(repo_path, quote=True)}"'
    marker = ""
    if repo_path in unavailable_repos:
        attrs += ' data-unavailable="true"'
        marker = ' <span class="un">· unavailable</span>'
    return (
        f'<div class="wt"{attrs} title="{escape(str(title), quote=True)}">'
        f'<span class="repo">{escape(repo)}</span> · {escape(branch)}{marker}</div>'
    )


def _card(task: dict, unavailable_repos: set[str] = frozenset()) -> str:
    """An active task-card: status-class + ``data-status``/``data-task-id`` hooks +
    per-card glyph + optional reason badge + one ``.wt`` line per repo (grouped by
    task — ONE card with N worktree lines, never N cards).

    ⚠️ Markup-contract sibling: ``static/poller.js`` (``fillActiveCard`` / ``renderCard``)
    builds this SAME node client-side for tasks that appear mid-session (Decision B).
    This function is the authority; change one, change both — the classes, glyph,
    ``.badge``, and ``.wt`` shape must stay identical so a reload == the patched DOM.
    """
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
    wt_html = "".join(_worktree_line(wt, unavailable_repos) for wt in worktrees)

    return (
        f'<div class="card {cls}" data-status="{escape(status, quote=True)}" '
        f'data-task-id="{escape(task_id, quote=True)}">'
        f'<div class="t"><span class="g">{glyph}</span>{escape(task_id)}{badge}</div>'
        f"{wt_html}</div>"
    )


def _column(status: str, tasks: list[dict], unavailable_repos: set[str] = frozenset()) -> str:
    """One active column: ``col-h`` header (glyph + label + count) over its cards.

    Empty-state copy (UX-DR9): an empty column is quiet (header + "0", NO placeholder
    card) — EXCEPT the **Blocked** column, the only one with affirmative empty copy
    ("Nothing needs you" — confirming "I'm clear" is a feature). That element is ALWAYS
    emitted and toggled ``hidden`` by card presence, so the poller can show/hide it live
    (``applyEmptyStates``) without the copy string living in the JS.
    """
    cls = tokens.STATUS_CLASS[status]
    glyph = tokens.STATUS_GLYPH[status]
    label = tokens.STATUS_LABEL[status]
    cards = [t for t in tasks if (t.get("status") or "") == status]
    cards_html = "".join(_card(t, unavailable_repos) for t in cards)
    extra = ""
    if status == "blocked":
        hidden = " hidden" if cards else ""
        extra = f'<div class="empty blk-empty"{hidden}>Nothing needs you</div>'
    return (
        f'<div class="col col-{cls}">'
        f'<div class="col-h">{glyph} {escape(label)} <span class="n">{len(cards)}</span></div>'
        f"{cards_html}{extra}</div>"
    )


def _donecard(task: dict, unavailable_repos: set[str] = frozenset()) -> str:
    """A folded done entry: ✓ glyph + task id + its ``repo · branch`` worktrees. Carries
    ``data-status="done"``/``data-task-id`` for the 2.4b diff-and-patch contract."""
    task_id = str(task.get("task_id", ""))
    glyph = tokens.STATUS_GLYPH["done"]
    worktrees = task.get("worktrees") or ()
    wt_html = "".join(_worktree_line(wt, unavailable_repos) for wt in worktrees)
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


def _orphan_disclosure(orphans: list[dict]) -> str:
    """The collapsed orphan disclosure below the done-disclosure (UX-DR7): a
    ``<details class="fold orphan">``, no ``open`` (NEVER auto-expanded even when
    non-empty), summary = the plain count, body = one self-explaining line per orphan.
    Zero orphans → returns ``""`` (the zero-orphan omission, AC4)."""
    if not orphans:
        return ""
    n = len(orphans)
    summary = f"{n} orphaned annotation" + ("" if n == 1 else "s")
    body = "".join(
        f'<div class="o"><b>{escape(o.get("branch", ""))}</b>'
        " — branch gone from git, note preserved here</div>"
        for o in orphans
    )
    return (
        f'<details class="fold orphan"><summary>{escape(summary)}</summary>'
        f'<div class="body">{body}</div></details>'
    )


def _fresh_stamp(generated_at: str, now_ms: int | None) -> str:
    """The subordinate freshness stamp. Always carries ``data-generated-at`` + the
    ``data-stale-threshold-ms`` the client poller reads (Decision A — live staleness is
    client-side). When ``now_ms`` is INJECTED (Decision B, deterministic — tests/preview),
    the server also renders the stale-at-load treatment: the ``stale`` class (grey→amber,
    a class SWAP, not a transition) + the explicit "stale — git unavailable" marker
    (UX-DR8). In production ``now_ms`` is ``None`` (purity — no clock read): the raw stamp
    ships and the client computes staleness live."""
    attrs = (
        f' data-generated-at="{escape(generated_at, quote=True)}"'
        f' data-stale-threshold-ms="{_STALE_THRESHOLD_MS}"'
    )
    if now_ms is None:
        return f'<span class="fresh"{attrs}>{escape(generated_at)}</span>'
    stale, label = _staleness(generated_at, now_ms, _STALE_THRESHOLD_MS)
    if not label:  # unparseable stamp — fall back to the raw value, no stale class
        return f'<span class="fresh"{attrs}>{escape(generated_at)}</span>'
    if stale:
        return (
            f'<span class="fresh stale"{attrs}>{escape(label)} '
            '<span class="stale-marker">stale — git unavailable</span></span>'
        )
    return f'<span class="fresh"{attrs}>{escape(label)}</span>'


def render_board(snapshot: dict, *, now_ms: int | None = None) -> str:
    """Render the full board HTML page for a ``/state`` snapshot dict.

    Pure and total: never reads a clock, never raises on a partial/orphan/warning
    payload. Tasks with an unknown/absent status (e.g. an untracked crash-orphan slug,
    ``status=None``) are not placed in any column or disclosure (kept total by skipping).

    ``now_ms`` (Story 2.4c, Decision B) is an OPTIONAL injected wall-clock in epoch ms,
    used ONLY for a deterministic stale-at-load render (the ``stale`` class + "stale — git
    unavailable" marker on an aged ``generated_at``). Production callers (the ``/`` route)
    pass nothing → ``now_ms is None`` → no clock is read and the client computes staleness
    live. Tests inject it for an HTML-output assertion on the stale treatment.
    """
    tasks: list[dict] = list(snapshot.get("tasks") or ())
    generated_at = str(snapshot.get("generated_at", ""))
    unavailable_repos, orphans = _warnings_index(snapshot.get("warnings") or [])

    # Counts grouped by status (only the four known statuses participate in pills/
    # columns/disclosure; unknown-status tasks are skipped — they belong to no column).
    counts: Counter[str] = Counter(
        t.get("status") for t in tasks if t.get("status") in tokens.STATUS_GLYPH
    )

    # Summary bar: one pill per status in fixed order (incl. done) + freshness stamp.
    pills = "".join(
        _summary_pill(s, counts.get(s, 0)) for s in ("running", "blocked", "review", "done")
    )
    summary = f'<div class="summary">{pills}{_fresh_stamp(generated_at, now_ms)}</div>'

    # Board: exactly three active columns in lifecycle order (done is NOT a column).
    cols = "".join(_column(s, tasks, unavailable_repos) for s in tokens.ACTIVE_COLUMNS)
    # Fully-empty-board line (UX-DR9): shown only when NO active task exists; always
    # emitted (toggled `hidden`) so the poller can reveal/hide it live without the
    # `create_task` copy living in the JS. `<code>` renders the backtick styling.
    active_count = sum(1 for t in tasks if (t.get("status") or "") in tokens.ACTIVE_COLUMNS)
    eb_hidden = " hidden" if active_count else ""
    empty_board = (
        f'<div class="empty-board"{eb_hidden}>'
        "No active tasks — create one with <code>create_task</code></div>"
    )
    board = f'<div class="cols">{cols}</div>{empty_board}'

    # Done-disclosure: collapsed (no `open`), summary "✓ N done", dimmed donecards.
    # Zero done → OMIT the disclosure entirely (UX-DR9 zero-done case).
    done_tasks = [t for t in tasks if (t.get("status") or "") == "done"]
    done_fold = ""
    if done_tasks:
        done_glyph = tokens.STATUS_GLYPH["done"]
        done_body = "".join(_donecard(t, unavailable_repos) for t in done_tasks)
        done_fold = (
            f'<details class="fold done"><summary>{done_glyph} {len(done_tasks)} done</summary>'
            f'<div class="body">{done_body}</div></details>'
        )

    # Orphan-disclosure below the done-disclosure: collapsed, omitted when zero.
    orphan_fold = _orphan_disclosure(orphans)

    # ── Story 2.4b live-poll wiring (no markup-contract change) ──
    # (a) the browser poll cadence, read by the poller off the .pg root;
    # (b) the initial snapshot the poller seeds ``prev`` from, so the first poll
    #     diffs against the server-rendered state (not an empty board). Escape ``<``
    #     to ``\\u003c`` so a ``</script>`` substring in any value cannot break out of
    #     the JSON <script> block (still valid JSON); and
    # (c) the poller itself, inlined (UX-DR10 — no external ``<script src>``).
    poll_ms = config.DASHBOARD_POLL_INTERVAL_MS
    initial_json = json.dumps(snapshot).replace("<", "\\u003c")
    initial_state = f'<script type="application/json" id="initial-state">{initial_json}</script>'
    poller_script = f"<script>{_POLLER_JS}</script>"

    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>dev-helper-mcp</title>"
        f"<style>{_STYLE}</style></head>"
        f'<body><div class="pg" data-poll-interval="{poll_ms}">'
        f"{summary}{board}{done_fold}{orphan_fold}</div>"
        f"{initial_state}{poller_script}</body></html>"
    )
