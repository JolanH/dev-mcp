"""Per-repo mutation mutex serializes same-repo work; different repos run free (AC 5).

The first group exercises the :class:`RepoLockRegistry` primitive directly. The
Story 1.5 group proves the real ``remove_worktree`` mutation acquires the per-repo
mutex (so a concurrent same-repo mutation is serialized, AR-14) while the
``list_worktrees`` read does NOT take it.
"""

import asyncio
import os
import subprocess

from dev_helper_mcp.core import tasks, worktrees
from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitRunner
from dev_helper_mcp.store import Store

# GIT_*-stripped env (gate-safe; see conftest tmp_git_repo).
_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
_ENV["GIT_TERMINAL_PROMPT"] = "0"


def _git(repo, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_ENV)


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


def test_same_repo_is_serialized():
    async def main():
        reg = RepoLockRegistry()
        order: list[str] = []

        async def worker(name: str, hold: float):
            async with reg.lock_for("/repo/a"):
                order.append(f"{name}-enter")
                await asyncio.sleep(hold)
                order.append(f"{name}-exit")

        # `first` acquires immediately and holds; `second` must wait for release.
        await asyncio.gather(worker("first", 0.05), worker("second", 0.0))
        return order

    order = asyncio.run(main())
    # No interleave: first fully completes before second enters.
    assert order == ["first-enter", "first-exit", "second-enter", "second-exit"]


def test_different_repos_run_concurrently():
    async def main():
        reg = RepoLockRegistry()
        order: list[str] = []

        async def worker(name: str, repo: str):
            async with reg.lock_for(repo):
                order.append(f"{name}-enter")
                await asyncio.sleep(0.05)
                order.append(f"{name}-exit")

        await asyncio.gather(worker("a", "/repo/a"), worker("b", "/repo/b"))
        return order

    order = asyncio.run(main())
    # Both enter before either exits — they did not serialize.
    assert set(order[:2]) == {"a-enter", "b-enter"}


def test_lock_for_is_stable_per_path():
    async def main():
        reg = RepoLockRegistry()
        same = reg.lock_for("/x") is reg.lock_for("/x")
        different = reg.lock_for("/x") is reg.lock_for("/y")
        return same, different

    same, different = asyncio.run(main())
    assert same is True
    assert different is False


def test_lock_for_normalizes_equivalent_paths():
    # Path aliases for the SAME repo must share one lock, or the mutex is defeated.
    async def main():
        reg = RepoLockRegistry()
        base = reg.lock_for("/repo/a")
        return (
            base is reg.lock_for("/repo/a/"),  # trailing slash
            base is reg.lock_for("/repo/x/../a"),  # .. segment
            base is reg.lock_for("/repo/./a"),  # . segment
        )

    slash, dotdot, dot = asyncio.run(main())
    assert slash and dotdot and dot


# ── Story 1.5: remove_worktree holds the per-repo mutex; list does not (AR-14) ──


def test_remove_worktree_is_serialized_by_repo_mutex(tmp_path):
    """A held same-repo mutex blocks remove_worktree until released (no torn state)."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            locks = RepoLockRegistry()
            await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=locks,
                store=store,
            )
            order: list[str] = []
            # Simulate a concurrent same-repo mutation already in flight by holding
            # the very lock remove_worktree will try to acquire (same registry+path).
            lock = locks.lock_for(os.path.abspath(str(repo)))
            await lock.acquire()
            order.append("holder-acquired")

            async def do_remove():
                order.append("remove-start")
                data = await worktrees.remove_worktree(
                    "feat",
                    str(repo),
                    runner=GitRunner(),
                    locks=locks,
                    store=store,
                )
                order.append("remove-done")
                return data

            task = asyncio.ensure_future(do_remove())
            await asyncio.sleep(0.05)  # let do_remove reach (and block on) the acquire
            order.append("still-holding")
            blocked = "remove-done" not in order
            lock.release()
            data = await task
            order.append("released")
            return order, blocked, data
        finally:
            await store.close()

    order, blocked, data = asyncio.run(main())
    # remove_worktree did NOT complete while the mutex was held.
    assert blocked is True
    assert order == [
        "holder-acquired",
        "remove-start",
        "still-holding",
        "remove-done",
        "released",
    ]
    assert data["task_closed"] is True  # ran to completion once unblocked


def test_list_worktrees_does_not_take_the_mutex(tmp_path):
    """list_worktrees completes even while the repo mutex is held (read = no mutex)."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            locks = RepoLockRegistry()
            await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=locks,
                store=store,
            )
            # Hold the repo mutex; a read that (wrongly) took it would deadlock —
            # wait_for turns that into a clean failure instead of a hang.
            await locks.lock_for(os.path.abspath(str(repo))).acquire()
            result = await asyncio.wait_for(
                worktrees.list_worktrees(repo=None, task_id=None, runner=GitRunner(), store=store),
                timeout=2.0,
            )
            return result
        finally:
            await store.close()

    result = asyncio.run(main())
    assert len(result) == 1
    assert result[0]["task_id"] == "feat"
