"""Ephemeral in-memory derive-on-read cache + its background refresher (Story 2.2).

This module is the **only writer of the in-memory view** (architecture.md:765-781):
it rebuilds an immutable :class:`~dev_helper_mcp.projection.CacheSnapshot` from a
per-repo ``git worktree list --porcelain`` fan-out on the READ pool and the
committed Store task rows, joins them through the pure Story 2.1 ``project()``,
and swaps the cache ref **whole** (a single GIL-atomic assignment — never an
in-place mutation). ``/state`` (Story 2.3) and read tools serve ``current``.

Invariant 4 (derive-on-read): the cache is ephemeral — rebuilt from scratch on
every tick and on every mutation; nothing derived is ever persisted. Invariant 7
(SDK seam): NO ``mcp``/``starlette`` import here (policed by
``tests/test_adapter_seam.py``); the refresher *loop* is also SDK-free — only the
``asyncio.create_task``/``cancel`` lifecycle is adapter-owned (server_factory).

Totality is the load-bearing property: a mutating tool calls :meth:`Cache.refresh`
*after* a committed git mutation, so ``refresh()`` must NEVER raise — a flaky git
read must not turn a successful mutation into a reported failure. Worst case it
keeps last-known state (AC3 "never blank").
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

from .errors import DevHelperError
from .git.porcelain import WorktreeEntry, parse_worktree_porcelain
from .git.runner import GitRunner, Pool
from .projection import CacheSnapshot, project
from .store import Store
from .util import now_iso

logger = logging.getLogger(__name__)


class Cache:
    """The ephemeral in-memory derive-on-read view + its rebuild logic.

    Holds the injected ``GitRunner`` + ``Store`` (constructed in the lifespan,
    loop-bound — the Cache does NOT construct them, per "asyncio objects live in
    the running loop"). Reuses the SAME ``GitRunner`` the ``ToolDeps`` hold so the
    READ-pool concurrency cap stays global (one pair of semaphores per app).
    """

    def __init__(self, *, runner: GitRunner, store: Store) -> None:
        self._runner = runner
        self._store = store
        #: The current immutable snapshot, swapped whole. Seeded empty so a brand-new
        #: server serves a valid (empty) board, not None, before the first tick.
        self._current: CacheSnapshot = CacheSnapshot(generated_at=now_iso(), tasks=(), warnings=())
        #: Per-repo last-successful porcelain, carried forward on a failed read so a
        #: transient-unavailable repo renders last-known rather than flipping orphaned.
        self._last_listings: dict[str, list[WorktreeEntry]] = {}
        #: Monotonic refresh-start counter. Each refresh() stamps a sequence at entry;
        #: only the latest-STARTED refresh is allowed to swap. This makes "freshest
        #: wins" hold for concurrent refreshes (a background tick that read the
        #: pre-mutation DB cannot clobber a later, post-mutation handler refresh that
        #: already swapped). Mutated only between awaits → no lock needed (AC2).
        self._refresh_seq: int = 0

    @property
    def current(self) -> CacheSnapshot:
        """The current snapshot, returned **by reference, no lock**.

        The read path (``/state`` and read tools). A single ref read is atomic and a
        swap can never produce a torn snapshot — the snapshot is frozen and replaced
        whole, never mutated in place.
        """
        return self._current

    async def refresh(self) -> None:
        """Rebuild the snapshot from a per-repo READ-pool fan-out + the 2.1 join.

        **Total — it never raises** (mirrors the projection's totality one layer up).
        Algorithm (story Dev Notes "The refresh algorithm"):

        1. Read the full committed task set; on any error keep last-known and return.
        2. Derive the distinct repo set from the links.
        3. Fan out ``_read_repo`` per repo on the READ pool (each total).
        4. Build ``git_listings`` from successful reads; carry forward last-known for
           a failed read; collect the unavailable repos.
        5. Project (pure 2.1 join) with a freshly stamped ``generated_at``.
        6. Merge ``repo_unavailable:<repo>`` warnings (shape-preserving).
        7. Total-failure → keep last-known (let ``generated_at`` age); else swap —
           but only if no newer refresh started while we were awaiting (freshest wins).
        """
        # Stamp this refresh's start order. The increment + read is a single
        # event-loop step (no await between), so concurrent refreshes get distinct,
        # ordered sequences. Only the latest-started one swaps (see the guard below).
        self._refresh_seq += 1
        my_seq = self._refresh_seq

        try:
            tasks = await self._store.list_tasks()
        except Exception:  # noqa: BLE001 — store unreadable: keep last-known (AC3 never blank)
            logger.warning(
                "cache refresh: store.list_tasks() failed; keeping last-known", exc_info=True
            )
            return

        repos = sorted({wt["repo_path"] for t in tasks for wt in t["worktrees"]})
        repo_set = set(repos)

        # Concurrent per-repo fan-out on the READ pool; each repo degrades
        # independently. asyncio.gather preserves order, but _read_repo returns the
        # repo_path so the mapping stays explicit and order-independent.
        results = await asyncio.gather(*[self._read_repo(r) for r in repos])

        git_listings: dict[str, list[WorktreeEntry]] = {}
        unavailable: list[str] = []
        for repo, entries in results:
            if entries is not None:
                git_listings[repo] = entries
                self._last_listings[repo] = entries  # record last-known
            else:
                unavailable.append(repo)
                last = self._last_listings.get(repo)
                if last is not None:
                    git_listings[repo] = last  # carry forward last-known

        # Evict last-known for repos no current task references (a removed task's repo).
        # Bounds growth and prevents resurrecting a months-old listing if that repo is
        # re-added later and its first read fails.
        self._last_listings = {r: e for r, e in self._last_listings.items() if r in repo_set}

        snapshot = project(git_listings=git_listings, tasks=tasks, generated_at=now_iso())

        if unavailable:
            # The projection only knows present-vs-absent; only the cache knows a read
            # FAILED. Add the per-repo signal without changing 2.1's frozen shape.
            snapshot = dataclasses.replace(
                snapshot,
                warnings=tuple(
                    sorted(snapshot.warnings + tuple(f"repo_unavailable:{r}" for r in unavailable))
                ),
            )

        if repos and len(unavailable) == len(repos):
            # Total failure (and there WERE repos to read): keep last-known so its
            # generated_at ages and the UI (2.4c) labels the whole board stale —
            # never blank. A partial degrade DOES swap (the healthy majority is fresh).
            logger.warning(
                "cache refresh: all %d repo(s) unavailable; keeping last-known snapshot",
                len(repos),
            )
            return

        if my_seq < self._refresh_seq:
            # A newer refresh started after us (it read fresher committed DB state).
            # Skip our swap so the latest-started refresh wins — never let an older,
            # in-flight tick clobber a post-mutation handler refresh (AC2).
            return

        self._current = snapshot  # single GIL-atomic ref swap

    async def _read_repo(self, repo_path: str) -> tuple[str, list[WorktreeEntry] | None]:
        """Read one repo's porcelain on the READ pool. **Total** — never raises.

        Returns ``(repo_path, entries)`` on success, ``(repo_path, None)`` on any
        failure (``DevHelperError`` raised by ``run_git`` — ``GitTimeout``/``Internal``
        — OR a non-zero ``returncode`` from a vanished/not-a-repo path). Mirrors the
        proven degrade pattern in ``core.worktrees.list_worktrees``.

        NO ``-z``: git 2.34 on this machine errors on ``worktree list -z``; the
        non-``-z`` form + the delimiter-agnostic parser is the established choice.
        """
        try:
            result = await self._runner.run_git(
                repo_path, ["worktree", "list", "--porcelain"], pool=Pool.READ
            )
        except DevHelperError as exc:
            logger.warning("cache: worktree list raised for %s (%s)", repo_path, exc.code)
            return (repo_path, None)
        if result.returncode != 0:
            logger.warning(
                "cache: worktree list failed for %s (rc=%s)", repo_path, result.returncode
            )
            return (repo_path, None)
        return (repo_path, parse_worktree_porcelain(result.stdout))


async def run_refresher(cache: Cache, *, interval: float) -> None:
    """Background loop: ``refresh()`` every ``interval`` seconds until cancelled.

    SDK-free (lives in core ``cache.py``); the adapter (server_factory) owns only
    the ``asyncio.create_task``/``cancel`` lifecycle. ``CancelledError`` re-raises so
    shutdown cancels cleanly; any other escaping exception is logged (belt-and-
    suspenders — ``refresh()`` is already total) so a single bad tick never kills
    the loop.
    """
    while True:
        try:
            await cache.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must never kill the refresher
            logger.exception("cache refresher tick failed")
        await asyncio.sleep(interval)
