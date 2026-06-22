"""Per-``repo_path`` async mutation mutex (Invariant 12; core layer — no SDK import).

Two concurrent ``create_task``/``remove_worktree`` calls may touch the same repo
(e.g. ``repos=[x,y]`` and ``[y,z]``). The mutation pool is a concurrency *limiter*,
not mutual exclusion, so an async mutex keyed by ``repo_path`` serializes mutations
to the same repo. Read/refresh git ops never take this mutex. The process-global
lockfile (Story 3.1) is a different layer and guards only the process singleton.

Like the runner's pools, the locks must be created and used within one event loop;
construct one registry inside the running loop (app startup / per test) rather than
sharing a module-global across ``asyncio.run()`` calls.
"""

from __future__ import annotations

import asyncio
import os


class RepoLockRegistry:
    """Lazily mints and reuses one :class:`asyncio.Lock` per repo.

    The key is normalized with ``os.path.abspath`` (pure — no filesystem stat, so
    Invariant 6 holds) so that path aliases for the *same* repo — trailing slash,
    ``.``/``..`` segments, relative vs absolute — map to one lock. Without this
    the mutex would fail to serialize same-repo mutations reached via different
    strings (e.g. ``repos=[A]`` vs ``[A/]``). Symlink-level aliasing is out of
    scope for v1 (resolving it needs blocking FS I/O); callers should pass
    canonical absolute repo paths, which ``create_task`` validates upstream.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, repo_path: str) -> asyncio.Lock:
        """Return the (stable) mutex for ``repo_path`` — same object per repo."""
        key = os.path.abspath(repo_path)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
