"""Story 2.3: read-only ``/state`` JSON endpoint served from the in-memory cache.

Covers the three ACs + Decision A (deps-null window):

* AC1 — ``GET /state`` → 200, snake_case ``generated_at``/``tasks``/``warnings``,
  equal to ``dataclasses.asdict(cache.current)`` by reference, reflects a
  post-``create_task`` mutation, and spawns NO git on the bare GET poll path.
* AC2 — Origin matrix on ``/state`` (extended in ``test_middleware_origin.py``).
* AC3 — read-only: a non-GET is rejected without mutation (404 via the catch-all
  Mount fallthrough — see the test); the parent route table exposes no mutating
  dashboard route (exactly ``[Route("/state", GET), Mount("/")]``).
* Decision A — ``holder.deps is None`` (startup/teardown window) → 503 JSON body.

In-process ``httpx.ASGITransport`` harness, base URL ``http://127.0.0.1:<port>``,
wrapped in ``async with app.router.lifespan_context(app)`` (the transport does NOT
auto-run the lifespan). Async driven via ``asyncio.run`` (no pytest-asyncio). The
single ``create_task`` is run against the ``tmp_git_repo`` fixture only — never the
project repo (git-safety HARD RULE).
"""

import asyncio
import dataclasses
import json

import dev_helper_mcp.git.runner as runner_mod
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.routing import Mount, Route

from dev_helper_mcp.server_factory import _DepsHolder


def _find_state_holder(app) -> _DepsHolder:
    """White-box: reach the ``_DepsHolder`` the ``/state`` route closed over.

    The route and the tool closures share ONE holder (Task 4); introspecting the
    endpoint closure lets a test compare the response to the live cache snapshot.
    """
    for route in app.routes:
        if isinstance(route, Route) and route.path == "/state":
            for cell in route.endpoint.__closure__ or ():
                if isinstance(cell.cell_contents, _DepsHolder):
                    return cell.cell_contents
    raise AssertionError("no /state route closing over a _DepsHolder found")


# ── AC1: shape + by-ref + reflects mutation + no git on the poll path ──


def test_state_returns_live_snapshot_and_reflects_mutation(
    app, asgi_client_factory, base_url, tmp_git_repo, monkeypatch
):
    # Count every git subprocess so we can prove the bare GET /state spawns none.
    git_calls: list[int] = []
    orig_run_git = runner_mod.GitRunner.run_git

    async def counting_run_git(self, *args, **kwargs):
        git_calls.append(1)
        return await orig_run_git(self, *args, **kwargs)

    monkeypatch.setattr(runner_mod.GitRunner, "run_git", counting_run_git)

    async def _run():
        async with app.router.lifespan_context(app):
            holder = _find_state_holder(app)
            async with asgi_client_factory() as client:
                # ── bare GET on an empty cache: equals asdict(current), no git ──
                snap = holder.deps.cache.current
                n_before = len(git_calls)
                empty_resp = await client.get("/state")
                no_git_during_get = len(git_calls) == n_before

                # ── drive a real create_task over MCP so the cache refreshes ──
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
                                "task_name": "state-task",
                                "description": "d",
                                "repos": [str(tmp_git_repo)],
                            },
                        )
                after_resp = await client.get("/state")
            return snap, empty_resp, no_git_during_get, after_resp

    snap, empty_resp, no_git_during_get, after_resp = asyncio.run(_run())

    # 200 + snake_case shape.
    assert empty_resp.status_code == 200
    body = empty_resp.json()
    assert set(body) == {"generated_at", "tasks", "warnings"}
    assert isinstance(body["generated_at"], str)
    assert isinstance(body["tasks"], list)
    assert isinstance(body["warnings"], list)
    # By-reference: the payload equals asdict(current) at call time (tuples→arrays).
    assert body == json.loads(json.dumps(dataclasses.asdict(snap)))
    # No git spawned on the bare poll path (Invariant 4).
    assert no_git_during_get, "GET /state must not spawn git"

    # Reflects the post-mutation cache (proves it reads the LIVE cache, not a const).
    assert after_resp.status_code == 200
    task_ids = {t["task_id"] for t in after_resp.json()["tasks"]}
    assert "state-task" in task_ids


# ── AC3: read-only — non-GET → 405; the route table mutates nothing ──


def test_non_get_methods_are_rejected_without_mutation(app, asgi_client_factory):
    """A non-GET to /state never performs a mutating action (AC3).

    Routing reality (Starlette 1.3.1): the ``methods=["GET"]`` route returns only a
    *partial* match for POST/PUT/DELETE, while the catch-all ``Mount("/")`` behind it
    *fully* matches and short-circuits — so a non-GET falls through to the MCP app and
    is rejected there (404) rather than yielding the route's own 405. Either way no
    mutating action exists at /state: the rejection (>=400, never 2xx) is the
    read-only guarantee. The exhaustive proof is the route-table assertion below.
    """

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as client:
                return (
                    await client.post("/state"),
                    await client.put("/state"),
                    await client.delete("/state"),
                )

    for resp in asyncio.run(_run()):
        # Always 404 (never a 2xx): the GET-only route only PARTIAL-matches a non-GET,
        # so the catch-all Mount("/") full-matches and the MCP app 404s — /state itself
        # mutates nothing on any non-GET verb. (The exhaustive read-only proof is the
        # route-table assertion in test_route_table_is_read_only.)
        assert resp.status_code == 404


def test_route_table_is_read_only(app):
    """The parent app exposes exactly the explicit GET dashboard routes (/state, then
    the / board added by Story 2.4a) followed by the catch-all MCP Mount — no dashboard
    route that creates/modifies/removes a task or worktree. Both explicit routes are
    GET-only; neither mutates (the board is FR-10 read-only)."""
    routes = app.routes
    assert len(routes) == 3, f"expected [Route('/state'), Route('/'), Mount('/')], got {routes}"

    state_route, board_route, mount = routes
    for r, path in ((state_route, "/state"), (board_route, "/")):
        assert isinstance(r, Route)
        assert r.path == path
        assert "GET" in r.methods
        for verb in ("POST", "PUT", "DELETE", "PATCH"):
            assert verb not in r.methods, f"{path} must not accept {verb}"

    # Starlette normalizes Mount("/") to an empty path prefix.
    assert isinstance(mount, Mount)
    assert mount.path == ""


# ── Decision A: deps-null startup/teardown window → 503 JSON body ──


def test_state_503_when_deps_not_ready(app, asgi_client_factory):
    """Without the lifespan, ``holder.deps`` is None (the startup/teardown window) →
    503 with a JSON body, never a leaked stack trace or a blank 500."""

    async def _run():
        async with asgi_client_factory() as client:
            return await client.get("/state")

    resp = asyncio.run(_run())
    assert resp.status_code == 503
    assert resp.json() == {"detail": "server not ready"}
