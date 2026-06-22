"""core.tasks.create — happy path + preflight rejection (AC 1–5).

Unit-tests the orchestrator directly with injected ``GitRunner``/``RepoLockRegistry``
and a tmp-file ``Store`` (no server, no port). Async is driven with ``asyncio.run``
(no pytest-asyncio); the deps are built inside each loop.
"""

import asyncio
import os
import subprocess

import pytest

from dev_helper_mcp.config import branch_name_for, worktree_path_for
from dev_helper_mcp.core import tasks
from dev_helper_mcp.errors import (
    ActiveTaskConflict,
    BaseRefNotFound,
    BranchExists,
    InvalidTaskName,
    NotAGitRepo,
    WorktreePathInUse,
)
from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitRunner
from dev_helper_mcp.store import Store

# Strip inherited GIT_* vars (see tmp_git_repo in conftest): under a git hook
# git exports GIT_DIR / GIT_INDEX_FILE / etc., which would redirect these
# subprocess git calls at the outer repo instead of the per-test tmp repo.
_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
_ENV["GIT_TERMINAL_PROMPT"] = "0"


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_ENV,
    ).stdout.strip()


def _init_repo(path) -> None:
    """Initialize a real git repo with one commit on branch ``main`` at ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


def _branch_exists(repo, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            env=_ENV,
        ).returncode
        == 0
    )


def _make_store(tmp_path):
    return Store.open(tmp_path / "state.db")


# ── AC 1: single-repo success ──


def test_single_repo_success(tmp_git_repo, tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            data = await tasks.create(
                "My Feature",
                "do the thing",
                [str(tmp_git_repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            task_row = await store.get_task("my-feature")
            n_wt = await store.count_worktrees("my-feature")
            return data, task_row, n_wt
        finally:
            await store.close()

    data, task_row, n_wt = asyncio.run(run())

    # Returned data shape / keys (snake_case, exact).
    assert data["task_id"] == "my-feature"
    assert data["status"] == "running"
    assert len(data["worktrees"]) == 1
    wt = data["worktrees"][0]
    assert set(wt) == {"repo_path", "worktree_path", "branch"}
    assert wt["repo_path"] == os.path.abspath(str(tmp_git_repo))
    assert wt["branch"] == "agent/my-feature"
    expected_path = str(worktree_path_for(tmp_git_repo, "my-feature"))
    assert wt["worktree_path"] == expected_path

    # Real git side effects.
    assert _branch_exists(tmp_git_repo, "agent/my-feature")
    assert os.path.isdir(expected_path)

    # Persistence: 1 task row (running) + 1 worktree row.
    assert task_row is not None
    assert task_row["status"] == "running"
    assert task_row["description"] == "do the thing"
    assert task_row["created_at"] == task_row["updated_at"]  # brand-new slug
    assert n_wt == 1


# ── AC 2: multi-repo all-or-nothing, single transaction ──


def test_multi_repo_success(tmp_path):
    repos = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]
    for r in repos:
        _init_repo(r)

    async def run():
        store = await _make_store(tmp_path)
        try:
            data = await tasks.create(
                "multi",
                "spanning",
                [str(r) for r in repos],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            return data, await store.count_worktrees("multi")
        finally:
            await store.close()

    data, n_wt = asyncio.run(run())

    assert len(data["worktrees"]) == 3
    assert n_wt == 3  # all rows committed in the single transaction
    for r in repos:
        assert _branch_exists(r, "agent/multi")
        assert os.path.isdir(str(worktree_path_for(r, "multi")))


# ── AC 3: base_ref honored ──


def test_base_ref_honored(tmp_git_repo, tmp_path):
    # A second commit on a `feature` branch; create from it and assert HEAD matches.
    _git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    (tmp_git_repo / "f.txt").write_text("feature\n")
    _git(tmp_git_repo, "add", "-A")
    _git(tmp_git_repo, "commit", "-q", "-m", "feature commit")
    feature_oid = _git(tmp_git_repo, "rev-parse", "feature")
    _git(tmp_git_repo, "checkout", "-q", "main")

    async def run():
        store = await _make_store(tmp_path)
        try:
            return await tasks.create(
                "from-feature",
                "branch from feature",
                [str(tmp_git_repo)],
                base_ref="feature",
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
        finally:
            await store.close()

    data = asyncio.run(run())
    wt_path = data["worktrees"][0]["worktree_path"]
    assert _git(wt_path, "rev-parse", "HEAD") == feature_oid


# ── AC 4: active-slug conflict (review/blocked conflict; done re-tasks) ──


def test_review_status_conflicts(tmp_git_repo, tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "task-x",
                "first",
                [str(tmp_git_repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            # Move to `review` directly via the Store (update_task not built yet).
            await store._conn.execute(
                "UPDATE task SET status = 'review' WHERE task_id = ?", ("task-x",)
            )
            await store._conn.commit()
            with pytest.raises(ActiveTaskConflict):
                await tasks.create(
                    "task-x",
                    "second",
                    [str(tmp_git_repo)],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
        finally:
            await store.close()

    asyncio.run(run())


def test_blocked_status_conflicts(tmp_git_repo, tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "task-b",
                "first",
                [str(tmp_git_repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            await store._conn.execute(
                "UPDATE task SET status = 'blocked' WHERE task_id = ?", ("task-b",)
            )
            await store._conn.commit()
            with pytest.raises(ActiveTaskConflict):
                await tasks.create(
                    "task-b",
                    "second",
                    [str(tmp_git_repo)],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
        finally:
            await store.close()

    asyncio.run(run())


def test_done_status_allows_retask(tmp_git_repo, tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            first = await tasks.create(
                "redo",
                "first",
                [str(tmp_git_repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            orig = await store.get_task("redo")
            # Mark done AND tear down the worktree+branch so preflight passes again.
            await store._conn.execute(
                "UPDATE task SET status = 'done' WHERE task_id = ?", ("redo",)
            )
            await store._conn.commit()
            _git(
                tmp_git_repo,
                "worktree",
                "remove",
                "--force",
                first["worktrees"][0]["worktree_path"],
            )
            _git(tmp_git_repo, "branch", "-D", "agent/redo")

            second = await tasks.create(
                "redo",
                "second",
                [str(tmp_git_repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            after = await store.get_task("redo")
            return orig, after, second
        finally:
            await store.close()

    orig, after, second = asyncio.run(run())
    assert second["task_id"] == "redo"
    assert after["status"] == "running"  # re-tasked
    assert after["description"] == "second"
    assert after["created_at"] == orig["created_at"]  # created_at preserved
    assert after["updated_at"] >= orig["updated_at"]  # updated_at advanced


# ── AC 5: preflight collision detection — nothing mutated ──


def test_branch_exists_preflight_no_mutation(tmp_path):
    repos = [tmp_path / "a", tmp_path / "b"]
    for r in repos:
        _init_repo(r)
    # Pre-create the colliding branch in the SECOND repo only.
    _git(repos[1], "branch", "agent/dup")

    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(BranchExists):
                await tasks.create(
                    "dup",
                    "x",
                    [str(r) for r in repos],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
            return await store.get_task("dup")
        finally:
            await store.close()

    row = asyncio.run(run())
    # No DB row, and no worktree created in ANY repo.
    assert row is None
    for r in repos:
        assert not os.path.isdir(str(worktree_path_for(r, "dup")))
    # The first repo must not have gained the agent branch.
    assert not _branch_exists(repos[0], "agent/dup")


def test_worktree_path_in_use_preflight(tmp_git_repo, tmp_path):
    # Pre-create the target worktree directory so preflight rejects it.
    wt = worktree_path_for(tmp_git_repo, "occupied")
    wt.mkdir(parents=True)

    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(WorktreePathInUse):
                await tasks.create(
                    "occupied",
                    "x",
                    [str(tmp_git_repo)],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
            return await store.get_task("occupied")
        finally:
            await store.close()

    assert asyncio.run(run()) is None
    assert not _branch_exists(tmp_git_repo, "agent/occupied")


def test_not_a_git_repo_preflight(tmp_path):
    non_repo = tmp_path / "plain"
    non_repo.mkdir()

    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(NotAGitRepo):
                await tasks.create(
                    "x",
                    "y",
                    [str(non_repo)],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
        finally:
            await store.close()

    asyncio.run(run())


def test_invalid_task_name_preflight(tmp_git_repo, tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(InvalidTaskName):
                await tasks.create(
                    "!!!",  # reduces to empty slug
                    "y",
                    [str(tmp_git_repo)],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
        finally:
            await store.close()

    asyncio.run(run())


def test_missing_base_ref_preflight(tmp_git_repo, tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(BaseRefNotFound):
                await tasks.create(
                    "x",
                    "y",
                    [str(tmp_git_repo)],
                    base_ref="no-such-ref",
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
            return await store.get_task("x")
        finally:
            await store.close()

    assert asyncio.run(run()) is None


def test_empty_repos_rejected(tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(InvalidTaskName):
                await tasks.create(
                    "x",
                    "y",
                    [],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
        finally:
            await store.close()

    asyncio.run(run())


def test_branch_name_helper():
    assert branch_name_for("foo") == "agent/foo"
