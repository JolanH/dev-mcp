"""tools.handlers.create_task — the {ok, data, error} envelope (AC 1, 4, 5).

Drives the adapter handler directly with real injected deps (no server/port).
"""

import asyncio

from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitRunner
from dev_helper_mcp.store import Store
from dev_helper_mcp.tools.handlers import ToolDeps, create_task
from dev_helper_mcp.tools.models import CreateTaskIn


def _deps(store):
    return ToolDeps(runner=GitRunner(), locks=RepoLockRegistry(), store=store)


def test_success_envelope(tmp_git_repo, tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            return await create_task(
                CreateTaskIn(task_name="feat", description="d", repos=[str(tmp_git_repo)]),
                deps=_deps(store),
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert set(env) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["error"] is None
    assert env["data"]["task_id"] == "feat"
    assert env["data"]["status"] == "running"


def test_typed_error_envelope(tmp_git_repo, tmp_path):
    """A typed DevHelperError → {ok:false, error:{code,...}}, all three keys present."""

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            deps = _deps(store)
            await create_task(
                CreateTaskIn(task_name="dup", description="d", repos=[str(tmp_git_repo)]),
                deps=deps,
            )
            await store._conn.execute("UPDATE task SET status='review' WHERE task_id='dup'")
            await store._conn.commit()
            return await create_task(
                CreateTaskIn(task_name="dup", description="d2", repos=[str(tmp_git_repo)]),
                deps=deps,
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert set(env) == {"ok", "data", "error"}
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "ActiveTaskConflict"
    assert "message" in env["error"]


def test_not_a_repo_error_envelope(tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            non_repo = tmp_path / "plain"
            non_repo.mkdir()
            return await create_task(
                CreateTaskIn(task_name="x", description="d", repos=[str(non_repo)]),
                deps=_deps(store),
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert env["ok"] is False
    assert env["error"]["code"] == "NotAGitRepo"
