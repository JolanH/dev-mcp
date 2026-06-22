"""Per-repo mutation mutex serializes same-repo work; different repos run free (AC 5).

No real mutation exists yet (create_task is Story 1.3) — this exercises the
primitive directly.
"""

import asyncio

from dev_helper_mcp.git.repo_lock import RepoLockRegistry


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
