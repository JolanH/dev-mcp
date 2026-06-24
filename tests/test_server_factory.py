"""AC 2: MCP handshake + no-op tool, no 307 redirect (in-process)."""

import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.routing import Mount

INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def test_mount_resolves(app):
    """The MCP sub-app is mounted on the parent app."""
    assert any(isinstance(r, Mount) for r in app.routes), "expected a Mount route"


def test_mcp_request_has_no_307(app, asgi_client_factory):
    """A POST to /mcp must NOT 307-redirect (clean 200 once the lifespan runs)."""

    async def _run() -> httpx.Response:
        # ASGITransport does not auto-run the lifespan; drive it explicitly so
        # the StreamableHTTP session manager is initialised.
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as client:
                return await client.post(
                    "/mcp",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=INIT_REQUEST,
                )

    resp = asyncio.run(_run())
    assert resp.status_code != 307, f"unexpected redirect: {resp.headers.get('location')}"
    assert resp.status_code == 200


def test_handshake_and_list_tasks_roundtrip(app, asgi_client_factory, base_url):
    """Full SDK handshake completes and a real tool (list_tasks) round-trips on an
    empty store. (Repurposed from the removed ``ping`` round-trip — the handshake
    assertion is the valuable part; ``ping`` is gone as of Story 1.6.)"""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as http_client:
                async with streamable_http_client(
                    url=f"{base_url}/mcp",
                    http_client=http_client,
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool("list_tasks", {})

    result = asyncio.run(_run())
    assert result.isError is False
    # The {ok, data, error} envelope is serialised as JSON text content.
    assert result.content, "list_tasks returned no content"
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["data"] == []  # empty store


def test_exactly_five_tools_advertised(app, asgi_client_factory, base_url):
    """AC6: exactly the 5 final tools are advertised (``ping`` absent), each with an
    input schema. Enumerated over the in-process SDK handshake."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as http_client:
                async with streamable_http_client(
                    url=f"{base_url}/mcp",
                    http_client=http_client,
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return (await session.list_tools()).tools

    tools = asyncio.run(_run())
    names = {t.name for t in tools}
    assert names == {
        "create_task",
        "list_worktrees",
        "remove_worktree",
        "update_task",
        "list_tasks",
    }
    assert "ping" not in names
    assert len(names) == 5
    # Each tool advertises an input schema.
    for t in tools:
        assert t.inputSchema is not None


def test_update_and_list_tasks_tools_roundtrip(app, asgi_client_factory, base_url, tmp_git_repo):
    """update_task + list_tasks round-trip end-to-end, asserting the {ok,data,error}
    envelope + snake_case keys over the wire (parsed from JSON text content)."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as http_client:
                async with streamable_http_client(
                    url=f"{base_url}/mcp",
                    http_client=http_client,
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.call_tool(
                            "create_task",
                            {
                                "task_name": "task-tool",
                                "description": "d",
                                "repos": [str(tmp_git_repo)],
                            },
                        )
                        updated = await session.call_tool(
                            "update_task", {"task_id": "task-tool", "status": "review"}
                        )
                        listed = await session.call_tool("list_tasks", {})
                        return updated, listed

    updated, listed = asyncio.run(_run())
    update_payload = json.loads(updated.content[0].text)
    assert update_payload["ok"] is True
    assert update_payload["data"]["status"] == "review"
    list_payload = json.loads(listed.content[0].text)
    assert list_payload["ok"] is True
    assert list_payload["data"][0]["task_id"] == "task-tool"
    assert list_payload["data"][0]["status"] == "review"


def test_create_task_tool_registered_and_reachable(
    app, asgi_client_factory, base_url, tmp_git_repo
):
    """create_task is registered and its lifespan-built deps work end-to-end."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as http_client:
                async with streamable_http_client(
                    url=f"{base_url}/mcp",
                    http_client=http_client,
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = {t.name for t in tools.tools}
                        assert "create_task" in names
                        return await session.call_tool(
                            "create_task",
                            {
                                "task_name": "via-mcp",
                                "description": "round trip",
                                "repos": [str(tmp_git_repo)],
                            },
                        )

    result = asyncio.run(_run())
    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["data"]["task_id"] == "via-mcp"
    assert payload["data"]["worktrees"][0]["branch"] == "agent/via-mcp"


def test_list_and_remove_worktree_tools_registered_and_reachable(
    app, asgi_client_factory, base_url, tmp_git_repo
):
    """list_worktrees + remove_worktree are registered and round-trip end-to-end."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with asgi_client_factory() as http_client:
                async with streamable_http_client(
                    url=f"{base_url}/mcp",
                    http_client=http_client,
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        names = {t.name for t in (await session.list_tools()).tools}
                        await session.call_tool(
                            "create_task",
                            {
                                "task_name": "wt-tool",
                                "description": "d",
                                "repos": [str(tmp_git_repo)],
                            },
                        )
                        listed = await session.call_tool("list_worktrees", {})
                        removed = await session.call_tool(
                            "remove_worktree",
                            {"task_id": "wt-tool", "repo": str(tmp_git_repo)},
                        )
                        return names, listed, removed

    names, listed, removed = asyncio.run(_run())
    assert {"list_worktrees", "remove_worktree"} <= names
    list_payload = json.loads(listed.content[0].text)
    assert list_payload["ok"] is True
    assert list_payload["data"][0]["task_id"] == "wt-tool"
    remove_payload = json.loads(removed.content[0].text)
    assert remove_payload["ok"] is True
    assert remove_payload["data"]["task_closed"] is True
