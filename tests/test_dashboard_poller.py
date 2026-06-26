"""Story 2.4b — live-poller integration (AC1, AC3, AC4) on the Python side.

The DOM diff-and-patch behaviour (AC2/AC3 mechanics: ``diff(x,x)===[]``, reparent,
zero-write) is proven browser-free by the ``node --test`` suite (``tests/js/``). This
module covers what needs the real server:

* **AC1** — a ``create_task`` then an ``update_task(status=…)`` against a throwaway
  ``tmp_git_repo`` are reflected in the very next ``GET /state`` payload (the 2.2
  post-mutation refresh guarantees it), so a poll that diffs the new ``/state`` would
  re-render. The board page (``GET /``) is served with the poller inlined and seeds
  its first diff from the embedded initial ``/state`` JSON.
* **AC4** — the served board is read-only: GET-only routes, the inlined poller fetches
  only ``GET /state`` and exposes no mutating control (the exhaustive grep is in
  ``test_dashboard_static_lint.py``; here we assert the *served* page end to end).

In-process ``httpx.ASGITransport`` harness, base URL ``http://127.0.0.1:<port>``,
wrapped in ``async with app.router.lifespan_context(app)`` (the transport does NOT
auto-run the lifespan). Async driven via ``asyncio.run`` (no pytest-asyncio). Every
git-spawning tool call targets the ``tmp_git_repo`` fixture only — never the project
repo (git-safety HARD RULE; the autouse guard in conftest enforces it).
"""

import asyncio
import re

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


# ── AC1: a create + status update is reflected in the next /state poll ──


def test_live_update_reflected_in_next_state_poll(app, asgi_client_factory, base_url, tmp_git_repo):
    """create_task → update_task(status='blocked') is visible in the subsequent
    GET /state (what the browser poll fetches), with the new status. This is the
    server half of AC1; the DOM patch is covered by the node diff/spy tests."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as client:
                async with streamable_http_client(url=f"{base_url}/mcp", http_client=client) as (
                    read,
                    write,
                    _sid,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.call_tool(
                            "create_task",
                            {
                                "task_name": "live-task",
                                "description": "d",
                                "repos": [str(tmp_git_repo)],
                            },
                        )
                        after_create = await client.get("/state")
                        await session.call_tool(
                            "update_task",
                            {"task_id": "live-task", "status": "blocked"},
                        )
                        after_update = await client.get("/state")
            return after_create, after_update

    after_create, after_update = asyncio.run(_run())

    assert after_create.status_code == 200
    created = {t["task_id"]: t for t in after_create.json()["tasks"]}
    assert "live-task" in created, "a created task must appear in the next /state poll"
    assert created["live-task"]["status"] == "running"

    assert after_update.status_code == 200
    updated = {t["task_id"]: t for t in after_update.json()["tasks"]}
    assert updated["live-task"]["status"] == "blocked", "the status change must be reflected"


# ── AC1/AC4: the board page is served with the poller inlined, seeded, read-only ──


def test_board_page_serves_inlined_poller_seeded_from_state(app, asgi_client_factory, tmp_git_repo):
    """GET / returns the board with: the poller inlined (no external src), the initial
    /state JSON embedded for the poller to seed `prev`, the poll interval injected, and
    a live task present — all over a real create_task against a tmp repo."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as client:
                base = str(client.base_url)
                async with streamable_http_client(url=f"{base}/mcp", http_client=client) as (
                    read,
                    write,
                    _sid,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.call_tool(
                            "create_task",
                            {
                                "task_name": "page-task",
                                "description": "d",
                                "repos": [str(tmp_git_repo)],
                            },
                        )
                return await client.get("/")

    resp = asyncio.run(_run())
    assert resp.status_code == 200
    html = resp.text
    low = html.lower()
    # Markup with <script> blocks stripped, so the read-only-control / external-asset
    # checks below can't false-match on the inlined poller JS or the embedded JSON.
    markup_low = re.sub(r"<script\b[^>]*>.*?</script>", "", low, flags=re.DOTALL)

    # Poller inlined (no external asset) + seeded + cadence injected.
    assert "function diff(" in html, "the poller must be inlined in the served page"
    # Anchor to a real <script ... src> element, not the bare "src=" substring.
    assert re.search(r"<script[^>]*\bsrc\b", low) is None, "no external <script src>"
    assert 'id="initial-state"' in low, "initial /state JSON must be embedded for seeding"
    assert "data-poll-interval=" in low, "poll cadence must be injected"
    assert 'fetch("/state"' in html, "the poller GETs /state"

    # The live task is on the server-rendered board (the poller then keeps it current).
    assert 'data-task-id="page-task"' in html

    # Read-only (FR-10): no mutating control reaches the page (scripts stripped first).
    for control in ("<form", "<button", "<input", "<textarea", "<select"):
        assert control not in markup_low, f"read-only board must not contain {control!r}"
