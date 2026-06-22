"""run_git(): two pools, timeout→kill+reap, fail-fast acquire, NotAGitRepo (AC 1, 2).

Async tests are driven with ``asyncio.run`` (no pytest-asyncio); a fresh
``GitRunner`` is built inside each loop so its semaphores belong to that loop.
"""

import asyncio
from pathlib import Path

import pytest

from dev_helper_mcp.errors import GitTimeout, Internal, NotAGitRepo
from dev_helper_mcp.git.runner import GitResult, GitRunner, Pool


def test_read_command_succeeds(tmp_git_repo):
    async def main():
        runner = GitRunner()
        return await runner.run_git(
            str(tmp_git_repo), ["rev-parse", "--is-inside-work-tree"], pool=Pool.READ
        )

    result = asyncio.run(main())
    assert isinstance(result, GitResult)
    assert result.returncode == 0
    assert result.stdout.strip() == b"true"


def test_mutation_pool_runs_command(tmp_git_repo):
    async def main():
        runner = GitRunner()
        return await runner.run_git(
            str(tmp_git_repo), ["status", "--porcelain"], pool=Pool.MUTATION
        )

    result = asyncio.run(main())
    assert result.returncode == 0


def test_dash_C_targets_the_repo(tmp_git_repo):
    async def main():
        runner = GitRunner()
        return await runner.run_git(
            str(tmp_git_repo), ["rev-parse", "--show-toplevel"], pool=Pool.READ
        )

    result = asyncio.run(main())
    assert result.returncode == 0
    assert Path(result.stdout.decode().strip()).resolve() == tmp_git_repo.resolve()


def test_nonzero_exit_is_returned_not_raised(tmp_git_repo):
    async def main():
        runner = GitRunner()
        return await runner.run_git(
            str(tmp_git_repo),
            ["rev-parse", "--verify", "refs/heads/does-not-exist"],
            pool=Pool.READ,
        )

    result = asyncio.run(main())
    assert result.returncode != 0
    assert result.stderr  # git wrote a fatal message


def test_timeout_raises_kills_and_releases_slot(tmp_git_repo):
    async def main():
        # Tiny command timeout forces the timeout path deterministically.
        runner = GitRunner(read_pool_size=2, read_timeout=0.001)
        with pytest.raises(GitTimeout):
            await runner.run_git(str(tmp_git_repo), ["rev-parse", "HEAD"], pool=Pool.READ)
        # Slot released in the finally (no leak / deadlock): value back to full.
        return runner._read_sem._value

    assert asyncio.run(main()) == 2


def test_read_acquire_timeout_fails_fast(tmp_git_repo):
    async def main():
        runner = GitRunner(read_pool_size=1, read_acquire_timeout=0.05)
        await runner._read_sem.acquire()  # occupy the only read slot
        try:
            with pytest.raises(GitTimeout):
                await runner.run_git(str(tmp_git_repo), ["rev-parse", "HEAD"], pool=Pool.READ)
        finally:
            runner._read_sem.release()

    asyncio.run(main())


def test_require_git_repo_accepts_real_repo(tmp_git_repo):
    async def main():
        runner = GitRunner()
        await runner.require_git_repo(str(tmp_git_repo))  # must not raise

    asyncio.run(main())


def test_require_git_repo_rejects_non_repo_dir(tmp_path):
    async def main():
        runner = GitRunner()
        with pytest.raises(NotAGitRepo):
            await runner.require_git_repo(str(tmp_path))

    asyncio.run(main())


def test_require_git_repo_rejects_missing_path(tmp_path):
    async def main():
        runner = GitRunner()
        with pytest.raises(NotAGitRepo):
            await runner.require_git_repo(str(tmp_path / "nope"))

    asyncio.run(main())


def test_missing_git_binary_raises_typed_internal(tmp_git_repo, monkeypatch):
    # No git on PATH → spawn fails; the layer must raise typed Internal, not a
    # raw FileNotFoundError (review finding: honor the error contract).
    monkeypatch.setenv("PATH", "/nonexistent-dir-for-test")

    async def main():
        runner = GitRunner()
        with pytest.raises(Internal):
            await runner.run_git(str(tmp_git_repo), ["rev-parse", "HEAD"], pool=Pool.READ)

    asyncio.run(main())


def test_read_acquire_timeout_does_not_leak_permit(tmp_git_repo):
    # A failed (timed-out) acquire must consume no permit — the pool stays usable.
    async def main():
        runner = GitRunner(read_pool_size=1, read_acquire_timeout=0.02)
        await runner._read_sem.acquire()  # occupy the only slot
        with pytest.raises(GitTimeout):
            await runner.run_git(str(tmp_git_repo), ["rev-parse", "HEAD"], pool=Pool.READ)
        runner._read_sem.release()  # release our manual hold
        assert runner._read_sem._value == 1  # pool fully restored, no leak
        res = await runner.run_git(str(tmp_git_repo), ["rev-parse", "HEAD"], pool=Pool.READ)
        return res.returncode

    assert asyncio.run(main()) == 0
