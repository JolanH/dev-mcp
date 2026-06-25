"""tools.handlers.create_task — the {ok, data, error} envelope (AC 1, 4, 5).

Drives the adapter handler directly with real injected deps (no server/port).
"""

import asyncio

from dev_helper_mcp.cache import Cache
from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitRunner
from dev_helper_mcp.store import Store
from dev_helper_mcp.tools.handlers import (
    ToolDeps,
    create_task,
    list_tasks,
    list_worktrees,
    remove_worktree,
    update_task,
)
from dev_helper_mcp.tools.models import (
    CreateTaskIn,
    ListTasksIn,
    ListWorktreesIn,
    RemoveWorktreeIn,
    UpdateTaskIn,
)


def _deps(store):
    # One shared GitRunner for the deps AND the cache (one pool pair per app).
    runner = GitRunner()
    return ToolDeps(
        runner=runner,
        locks=RepoLockRegistry(),
        store=store,
        cache=Cache(runner=runner, store=store),
    )


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


# ── list_worktrees / remove_worktree envelopes (Story 1.5) ──


def test_list_worktrees_success_envelope(tmp_git_repo, tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            deps = _deps(store)
            await create_task(
                CreateTaskIn(task_name="feat", description="d", repos=[str(tmp_git_repo)]),
                deps=deps,
            )
            return await list_worktrees(ListWorktreesIn(), deps=deps)
        finally:
            await store.close()

    env = asyncio.run(run())
    assert set(env) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["error"] is None
    assert len(env["data"]) == 1
    entry = env["data"][0]
    # snake_case keys only.
    assert set(entry) == {"task_id", "repo_path", "branch", "worktree_path", "status", "orphaned"}
    assert entry["task_id"] == "feat"
    assert entry["orphaned"] is False


def test_remove_worktree_success_envelope(tmp_git_repo, tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            deps = _deps(store)
            await create_task(
                CreateTaskIn(task_name="feat", description="d", repos=[str(tmp_git_repo)]),
                deps=deps,
            )
            return await remove_worktree(
                RemoveWorktreeIn(task_id="feat", repo=str(tmp_git_repo)), deps=deps
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert set(env) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["data"]["task_id"] == "feat"
    assert env["data"]["task_closed"] is True  # last worktree → task closed (AC5)


def test_remove_worktree_typed_error_envelope(tmp_path):
    """An unknown task → {ok:false, error:{code:'TaskNotFound'}} (error-as-data)."""

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            non_repo = tmp_path / "plain"
            non_repo.mkdir()
            return await remove_worktree(
                RemoveWorktreeIn(task_id="ghost", repo=str(non_repo)), deps=_deps(store)
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "TaskNotFound"


# ── update_task / list_tasks envelopes (Story 1.6) ──


def test_update_task_success_envelope(tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            await store.add_task(
                "t1", "first", "running", "2026-06-22T10:00:00Z", "2026-06-22T10:00:00Z"
            )
            return await update_task(
                UpdateTaskIn(task_id="t1", status="review", description="second"),
                deps=_deps(store),
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert set(env) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["error"] is None
    assert set(env["data"]) == {"task_id", "status", "description", "created_at", "updated_at"}
    assert env["data"]["status"] == "review"
    assert env["data"]["description"] == "second"


def test_update_task_not_found_envelope(tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            return await update_task(
                UpdateTaskIn(task_id="ghost", status="done"), deps=_deps(store)
            )
        finally:
            await store.close()

    env = asyncio.run(run())
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "TaskNotFound"


def test_update_task_invalid_status_envelope(tmp_path):
    """An out-of-set status is error-as-data (NOT a Pydantic ValidationError escaping)."""

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            await store.add_task(
                "t1", "d", "running", "2026-06-22T10:00:00Z", "2026-06-22T10:00:00Z"
            )
            return await update_task(UpdateTaskIn(task_id="t1", status="bogus"), deps=_deps(store))
        finally:
            await store.close()

    env = asyncio.run(run())
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "InvalidStatus"
    assert env["error"]["details"]["reason"] == "not_in_set"


def test_list_tasks_success_envelope(tmp_git_repo, tmp_path):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            deps = _deps(store)
            await create_task(
                CreateTaskIn(task_name="feat", description="d", repos=[str(tmp_git_repo)]),
                deps=deps,
            )
            return await list_tasks(ListTasksIn(), deps=deps)
        finally:
            await store.close()

    env = asyncio.run(run())
    assert set(env) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["error"] is None
    assert len(env["data"]) == 1
    task = env["data"][0]
    assert set(task) == {
        "task_id",
        "description",
        "status",
        "created_at",
        "updated_at",
        "worktrees",
    }
    assert task["task_id"] == "feat"
    assert task["worktrees"][0]["branch"] == "agent/feat"
