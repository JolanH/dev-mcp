"""The single off-loop git execution path (core layer — no SDK import).

Invariant 1: every ``git`` call in the codebase goes through :class:`GitRunner`
and its correct pool — never ``subprocess.run``/``os.system`` for git anywhere
else. Two latency classes get separate permit pools (arch §Async-git):

* **read/refresh** — 3s command timeout, semaphore=2, 2s acquire timeout
  (fail fast / keep the cache rather than queue);
* **mutation** — ~120s timeout, semaphore=4.

Only typed :class:`~dev_helper_mcp.errors.DevHelperError`s leave this layer:
``GitTimeout`` (acquire/command timeout), ``NotAGitRepo`` (preflight), and
``Internal`` (the git binary is missing/unrunnable). A non-zero git exit is
*returned* (in :class:`GitResult`) — callers classify ``BranchExists`` etc.

The two semaphores live on the instance, not at module scope, because the test
suite drives async code with ``asyncio.run()`` (a fresh event loop per call) and
asyncio primitives must not be shared across loops. Construct one ``GitRunner``
inside the running loop (app startup / per test).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import (
    GIT_ENV,
    GIT_MUTATION_POOL_SIZE,
    GIT_MUTATION_TIMEOUT,
    GIT_READ_ACQUIRE_TIMEOUT,
    GIT_READ_POOL_SIZE,
    GIT_READ_TIMEOUT,
)
from ..errors import GitTimeout, Internal, NotAGitRepo

logger = logging.getLogger(__name__)


class Pool(Enum):
    """Which latency-class permit pool a git command runs under."""

    READ = "read"
    MUTATION = "mutation"


@dataclass(frozen=True)
class GitResult:
    """Outcome of a git command. ``stdout`` stays bytes for ``-z`` parsing."""

    returncode: int
    stdout: bytes
    stderr: bytes


class GitRunner:
    """Owns the two permit pools and the single ``run_git`` entry point.

    Pool sizes and timeouts default to ``config`` but are injectable so tests can
    force the timeout / acquire-timeout / saturation paths deterministically.
    """

    def __init__(
        self,
        *,
        read_pool_size: int = GIT_READ_POOL_SIZE,
        mutation_pool_size: int = GIT_MUTATION_POOL_SIZE,
        read_timeout: float = GIT_READ_TIMEOUT,
        mutation_timeout: float = GIT_MUTATION_TIMEOUT,
        read_acquire_timeout: float = GIT_READ_ACQUIRE_TIMEOUT,
    ) -> None:
        self._read_sem = asyncio.Semaphore(read_pool_size)
        self._mutation_sem = asyncio.Semaphore(mutation_pool_size)
        self._read_timeout = read_timeout
        self._mutation_timeout = mutation_timeout
        self._read_acquire_timeout = read_acquire_timeout

    async def run_git(self, repo: str | Path, args: Sequence[str], *, pool: Pool) -> GitResult:
        """Run ``git -C <repo> <args>`` under ``pool``; never via a shell.

        Raises ``GitTimeout`` on acquire/command timeout (subprocess killed and
        reaped first). Non-zero exits return a :class:`GitResult`.
        """
        if pool is Pool.READ:
            await self._acquire_read()
            sem, timeout = self._read_sem, self._read_timeout
        else:
            await self._mutation_sem.acquire()
            sem, timeout = self._mutation_sem, self._mutation_timeout
        try:
            return await self._exec(repo, args, timeout)
        finally:
            sem.release()

    async def require_git_repo(self, repo: str | Path) -> None:
        """Raise ``NotAGitRepo`` unless ``repo`` is an existing git work tree.

        Preflight used by ``create_task`` (Story 1.3/1.4) before any mutation.
        """
        if not os.path.isdir(repo):
            raise NotAGitRepo("path is not a directory", {"repo": str(repo)})
        result = await self.run_git(repo, ["rev-parse", "--is-inside-work-tree"], pool=Pool.READ)
        if result.returncode != 0 or result.stdout.strip() != b"true":
            raise NotAGitRepo("path is not a git repository", {"repo": str(repo)})

    # ── internals ──

    async def _acquire_read(self) -> None:
        try:
            await asyncio.wait_for(self._read_sem.acquire(), self._read_acquire_timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise GitTimeout(
                f"git read pool acquire timed out after {self._read_acquire_timeout}s",
                {"phase": "acquire", "pool": "read"},
            ) from exc

    async def _exec(self, repo: str | Path, args: Sequence[str], timeout: float) -> GitResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **GIT_ENV},
            )
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            # Honor the contract: only typed errors leave this layer. A missing /
            # unrunnable git binary is an environment failure, not a tool error.
            raise Internal("git executable not found or not runnable", {"error": str(exc)}) from exc

        try:
            # communicate() drains both pipes to EOF — no pipe-buffer deadlock.
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            await self._kill_and_reap(proc)
            raise GitTimeout(
                f"git command exceeded {timeout}s",
                {"repo": str(repo), "args": list(args)},
            ) from exc
        except asyncio.CancelledError:
            # External cancel (shutdown / client disconnect) mid-run — never leave
            # the subprocess orphaned; kill, reap, then propagate the cancellation.
            await self._kill_and_reap(proc)
            raise
        return GitResult(returncode=proc.returncode, stdout=stdout, stderr=stderr)

    @staticmethod
    async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
        """Kill and reap ``proc`` (no zombie). Safe if it already exited."""
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # already gone — nothing to kill
        await proc.wait()
