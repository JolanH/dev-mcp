"""Story 2.2 — ephemeral in-memory cache + background refresher (AC 1-4).

Two test classes of fixtures, deliberately split by git-safety surface:

* **Fake-runner / fake-store unit tests** (degrade, stale, swap, carry-forward,
  totality, perf) — spawn NO git at all (a stub ``run_git`` returns canned
  ``GitResult``s / sleeps), so they are deterministic and have zero git-safety
  surface (project-context.md#Git safety in tests). This is the bulk.
* **Real-git pipeline tests** (the one end-to-end + the critical-section refresh)
  use ``tmp_git_repo`` + a real ``GitRunner`` + a tmp ``Store``. The HARD git-safety
  rule applies: every git op targets the tmp repo, never the project repo (the
  autouse ``_guard_project_repo_untouched`` + ``test_git_safety.py`` enforce it).

Async tests are driven with ``asyncio.run`` (no pytest-asyncio).
"""

import asyncio
import time

import pytest

from dev_helper_mcp.cache import Cache, run_refresher
from dev_helper_mcp.core import tasks
from dev_helper_mcp.errors import GitTimeout
from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitResult, GitRunner, Pool
from dev_helper_mcp.projection import CacheSnapshot
from dev_helper_mcp.store import Store
from dev_helper_mcp.tools.handlers import ToolDeps, create_task
from dev_helper_mcp.tools.models import CreateTaskIn

# ────────────────────────────── test doubles ──────────────────────────────


def _porcelain(*records: tuple[str, str | None]) -> bytes:
    """Build ``git worktree list --porcelain`` bytes from ``(path, branch)`` records.

    ``branch=None`` emits a detached record (no ``branch`` line). Newline form (no
    ``-z``) — exactly what the cache reads on git 2.34.
    """
    lines: list[str] = []
    for path, branch in records:
        lines.append(f"worktree {path}")
        lines.append("HEAD 0123456789abcdef0123456789abcdef01234567")
        if branch is not None:
            lines.append(f"branch refs/heads/{branch}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _task(task_id: str, links: list[tuple[str, str, str]]) -> dict:
    """A ``store.list_tasks()`` shaped row. ``links`` are ``(repo, branch, wt_path)``."""
    return {
        "task_id": task_id,
        "description": "desc",
        "status": "running",
        "created_at": "2026-06-25T12:00:00Z",
        "updated_at": "2026-06-25T12:00:00Z",
        "worktrees": [
            {"repo_path": repo, "branch": branch, "worktree_path": wt} for repo, branch, wt in links
        ],
    }


class FakeStore:
    """Minimal ``Store`` stand-in exposing only ``list_tasks`` (the cache's read)."""

    def __init__(self, tasks_rows: list[dict], *, raises: bool = False) -> None:
        self._rows = tasks_rows
        self._raises = raises

    async def list_tasks(self) -> list[dict]:
        if self._raises:
            raise RuntimeError("store boom")
        return self._rows


class FakeRunner:
    """Stub ``GitRunner.run_git`` returning canned results per repo.

    ``porcelain``: ``repo -> bytes``. ``fail_repos`` (mutable, so a test can flip a
    repo's health between ticks): repos that RETURN ``rc=1``. ``raise_repos``: repos
    that RAISE ``GitTimeout`` (the contended/hung path). ``delays``: per-repo
    ``asyncio.sleep`` to simulate a slow repo. Honors a READ-pool-sized semaphore so
    the slow-repo injector throttles like the real pool (``sem=2``).
    """

    def __init__(
        self,
        *,
        porcelain: dict[str, bytes],
        fail_repos: set[str] | None = None,
        raise_repos: set[str] | None = None,
        delays: dict[str, float] | None = None,
        pool_size: int = 2,
    ) -> None:
        self.porcelain = porcelain
        self.fail_repos = fail_repos if fail_repos is not None else set()
        self.raise_repos = raise_repos if raise_repos is not None else set()
        self.delays = delays or {}
        self._sem = asyncio.Semaphore(pool_size)

    async def run_git(self, repo, args, *, pool) -> GitResult:
        assert pool is Pool.READ  # the refresh path is READ-only (Invariant 1/10)
        key = str(repo)
        async with self._sem:
            if key in self.delays:
                await asyncio.sleep(self.delays[key])
            if key in self.raise_repos:
                raise GitTimeout("read pool acquire timed out", {"repo": key})
            if key in self.fail_repos:
                return GitResult(returncode=1, stdout=b"", stderr=b"fatal: not a git repo")
            return GitResult(returncode=0, stdout=self.porcelain.get(key, b""), stderr=b"")


def _cache(runner, store) -> Cache:
    return Cache(runner=runner, store=store)


def _find_task(snapshot: CacheSnapshot, task_id: str):
    return next(t for t in snapshot.tasks if t.task_id == task_id)


def _wt_for(task_view, repo_path: str):
    return next(w for w in task_view.worktrees if w.repo_path == repo_path)


# ───────────────────────── AC1 — atomic swap + by-ref read ─────────────────────────


def test_refresh_swaps_in_new_snapshot_instance_and_reads_by_ref():
    repo = "/fake/a"
    store = FakeStore([_task("feat", [(repo, "agent/feat", f"{repo}.wt/feat")])])
    runner = FakeRunner(porcelain={repo: _porcelain((f"{repo}.wt/feat", "agent/feat"))})

    async def run():
        cache = _cache(runner, store)
        seed = cache.current  # the empty seed snapshot
        await cache.refresh()
        after = cache.current
        # Two reads return the SAME object until the next swap (by-ref, no copy).
        assert cache.current is after
        return seed, after

    seed, after = asyncio.run(run())
    assert after is not seed  # identity changed → a genuine whole-snapshot swap
    assert isinstance(after, CacheSnapshot)
    assert [t.task_id for t in after.tasks] == ["feat"]
    wt = _wt_for(_find_task(after, "feat"), "/fake/a")
    assert wt.orphaned is False
    assert wt.path == "/fake/a.wt/feat"


def test_empty_snapshot_is_valid_not_stale():
    """Zero tasks → repos == [] → total-fail guard is FALSE → swap an empty snapshot."""
    store = FakeStore([])
    runner = FakeRunner(porcelain={})

    async def run():
        cache = _cache(runner, store)
        seed = cache.current
        await cache.refresh()
        return seed, cache.current

    seed, after = asyncio.run(run())
    assert after is not seed  # a brand-new server with no tasks shows an empty board
    assert after.tasks == ()
    assert after.warnings == ()


# ───────────────────── AC3 — per-repo degrade / stale / carry-forward ─────────────────────


def test_partial_degrade_swaps_with_repo_unavailable_warning_and_carry_forward():
    repo_a, repo_b = "/fake/a", "/fake/b"
    store = FakeStore(
        [
            _task(
                "feat",
                [
                    (repo_a, "agent/feat", f"{repo_a}.wt/feat"),
                    (repo_b, "agent/feat", f"{repo_b}.wt/feat"),
                ],
            )
        ]
    )
    runner = FakeRunner(
        porcelain={
            repo_a: _porcelain((f"{repo_a}.wt/feat", "agent/feat")),
            repo_b: _porcelain((f"{repo_b}.wt/feat", "agent/feat")),
        }
    )

    async def run():
        cache = _cache(runner, store)
        await cache.refresh()  # tick 1: both healthy
        first = cache.current
        # repo_b now goes unavailable; repo_a stays healthy → PARTIAL degrade.
        runner.raise_repos = {repo_b}
        await cache.refresh()  # tick 2
        return first, cache.current

    first, second = asyncio.run(run())
    # Partial degrade still swaps (the healthy majority is genuinely fresh).
    assert second is not first
    assert "repo_unavailable:/fake/b" in second.warnings
    feat = _find_task(second, "feat")
    # repo_b's worktree is carried-forward last-known → present, NOT orphaned.
    assert _wt_for(feat, repo_b).orphaned is False
    assert _wt_for(feat, repo_a).orphaned is False
    # No orphan_link warning for the carried-forward repo (it renders present).
    assert not any(w.startswith("orphan_link:") for w in second.warnings)


def test_degraded_repo_with_no_last_known_is_unavailable_and_orphaned():
    """First-tick failure with no history → repo omitted (link orphans) + warning."""
    repo_a, repo_b = "/fake/a", "/fake/b"
    store = FakeStore(
        [
            _task("feat", [(repo_a, "agent/feat", f"{repo_a}.wt/feat")]),
            _task("bug", [(repo_b, "agent/bug", f"{repo_b}.wt/bug")]),
        ]
    )
    runner = FakeRunner(
        porcelain={repo_a: _porcelain((f"{repo_a}.wt/feat", "agent/feat"))},
        fail_repos={repo_b},  # rc=1, never seen successfully → no carry-forward
    )

    async def run():
        cache = _cache(runner, store)
        await cache.refresh()
        return cache.current

    snap = asyncio.run(run())
    assert "repo_unavailable:/fake/b" in snap.warnings
    # The unavailable, never-seen repo's link surfaces orphaned (absent from listings),
    # with project()'s own orphan_link warning alongside the repo_unavailable signal.
    bug = _find_task(snap, "bug")
    assert _wt_for(bug, repo_b).orphaned is True
    assert "orphan_link:bug@/fake/b:agent/bug" in snap.warnings


def test_total_failure_keeps_last_known_and_does_not_swap():
    repo = "/fake/a"
    store = FakeStore([_task("feat", [(repo, "agent/feat", f"{repo}.wt/feat")])])
    runner = FakeRunner(porcelain={repo: _porcelain((f"{repo}.wt/feat", "agent/feat"))})

    async def run():
        cache = _cache(runner, store)
        await cache.refresh()  # tick 1: healthy
        good = cache.current
        # Every repo now unavailable → total failure → keep last-known (no swap).
        runner.raise_repos = {repo}
        await cache.refresh()  # tick 2
        return good, cache.current

    good, after = asyncio.run(run())
    assert after is good  # SAME object — generated_at ages, board never blanks
    assert [t.task_id for t in after.tasks] == ["feat"]


def test_refresh_is_total_when_store_unreadable():
    """store.list_tasks() raising → keep last-known, never raise (AC3)."""
    store = FakeStore([], raises=True)
    runner = FakeRunner(porcelain={})

    async def run():
        cache = _cache(runner, store)
        seed = cache.current
        await cache.refresh()  # must NOT raise
        return seed, cache.current

    seed, after = asyncio.run(run())
    assert after is seed  # nothing swapped — last-known (the empty seed) kept


def test_refresh_never_raises_on_any_injected_failure():
    repo_a, repo_b, repo_c = "/fake/a", "/fake/b", "/fake/c"
    store = FakeStore(
        [
            _task("a", [(repo_a, "agent/a", f"{repo_a}.wt/a")]),
            _task("b", [(repo_b, "agent/b", f"{repo_b}.wt/b")]),
            _task("c", [(repo_c, "agent/c", f"{repo_c}.wt/c")]),
        ]
    )
    runner = FakeRunner(
        porcelain={repo_a: _porcelain((f"{repo_a}.wt/a", "agent/a"))},
        raise_repos={repo_b},  # GitTimeout
        fail_repos={repo_c},  # rc=1
    )

    async def run():
        cache = _cache(runner, store)
        await cache.refresh()  # mixed raise + rc=1 + healthy → must not raise
        return cache.current

    snap = asyncio.run(run())  # reaching here proves totality
    assert "repo_unavailable:/fake/b" in snap.warnings
    assert "repo_unavailable:/fake/c" in snap.warnings


def test_distinct_repo_dedup_reads_shared_repo_once():
    """A repo shared by two tasks is read once (fan-out keyed by repo, not link)."""
    shared = "/fake/shared"
    store = FakeStore(
        [
            _task("alpha", [(shared, "agent/alpha", f"{shared}.wt/alpha")]),
            _task("beta", [(shared, "agent/beta", f"{shared}.wt/beta")]),
        ]
    )
    calls: list[str] = []

    class CountingRunner(FakeRunner):
        async def run_git(self, repo, args, *, pool):
            calls.append(str(repo))
            return await super().run_git(repo, args, pool=pool)

    runner = CountingRunner(
        porcelain={
            shared: _porcelain(
                (f"{shared}.wt/alpha", "agent/alpha"),
                (f"{shared}.wt/beta", "agent/beta"),
            )
        }
    )

    async def run():
        await _cache(runner, store).refresh()

    asyncio.run(run())
    assert calls == [shared]  # exactly one read despite two tasks touching it


# ───────────────────── background refresher loop ─────────────────────


def test_run_refresher_rebuilds_then_cancels_cleanly():
    repo = "/fake/a"
    store = FakeStore([_task("feat", [(repo, "agent/feat", f"{repo}.wt/feat")])])
    runner = FakeRunner(porcelain={repo: _porcelain((f"{repo}.wt/feat", "agent/feat"))})

    async def run():
        cache = _cache(runner, store)
        task = asyncio.create_task(run_refresher(cache, interval=0.01))
        # Let a few ticks run, then cancel — cancellation must propagate cleanly.
        for _ in range(50):
            if cache.current.tasks:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        # Clean cancellation REQUIRES run_refresher to re-raise CancelledError (not
        # swallow it): awaiting the task must raise, and task.cancelled() must be True.
        # If the loop swallowed the cancellation, this would NOT raise and the assert
        # below would fail — so the test actually verifies the property in its name.
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        return cache.current

    snap = asyncio.run(run())
    assert [t.task_id for t in snap.tasks] == ["feat"]


# ───────────────────── AC1 / AC2 — real-git pipeline (tmp_git_repo) ─────────────────────


def test_end_to_end_refresh_over_real_git(tmp_git_repo, tmp_path):
    """Fan-out → project → swap on REAL porcelain (HARD git-safety: tmp repo only)."""

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            runner = GitRunner()
            await tasks.create(
                "feat",
                "d",
                [str(tmp_git_repo)],
                runner=runner,
                locks=RepoLockRegistry(),
                store=store,
            )
            cache = Cache(runner=runner, store=store)
            await cache.refresh()
            return cache.current
        finally:
            await store.close()

    snap = asyncio.run(run())
    assert isinstance(snap, CacheSnapshot)
    feat = _find_task(snap, "feat")
    wt = _wt_for(feat, str(tmp_git_repo))
    assert wt.branch == "agent/feat"
    assert wt.orphaned is False
    assert feat.status == "running"


def test_create_task_handler_refreshes_cache_before_returning(tmp_git_repo, tmp_path):
    """AC2: a mutating handler refreshes the shared cache before the envelope returns."""

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            runner = GitRunner()
            cache = Cache(runner=runner, store=store)
            deps = ToolDeps(
                runner=runner,
                locks=RepoLockRegistry(),
                store=store,
                cache=cache,
            )
            # The cache is empty before the mutation.
            assert cache.current.tasks == ()
            env = await create_task(
                CreateTaskIn(
                    task_name="feat", description="d", repos=[str(tmp_git_repo)], base_ref="main"
                ),
                deps=deps,
            )
            return env, cache.current
        finally:
            await store.close()

    env, snap = asyncio.run(run())
    assert env["ok"] is True
    # The cache reflects the just-created task — refreshed inside the handler,
    # BEFORE the ok envelope was returned.
    assert [t.task_id for t in snap.tasks] == ["feat"]
    assert _wt_for(_find_task(snap, "feat"), str(tmp_git_repo)).orphaned is False


# ───────────────────── AC4 — fan-out perf / chaos (slow) ─────────────────────

#: The soft freshness SLO: p95 derive latency for the fan-out (architecture.md/FR-9).
#: It is an SLO bounded at ≤15 repos, NOT a hard guarantee — beyond ~15 tracked
#: repos the per-command READ timeout (3s) × the serialization the sem=2 pool
#: imposes pushes worst-case latency over the cliff. The parametrization stops at
#: 15 deliberately; we assert the bound only inside that envelope (see the
#: documented-cliff assertion below).
_SLO_SECONDS = 3.0
_MAX_VALIDATED_REPOS = 15


def _build_fanout(num_repos: int, *, slow_delay: float, tasks_per_repo: int = 1):
    """Build a (store, runner) pair across the spec's ``num_tasks × repos_per_task``
    matrix: ``num_repos`` DISTINCT repos (the variable the ≤3s SLO is bounded on),
    each carrying ``tasks_per_repo`` tasks (so ``num_repos × tasks_per_repo`` total
    tasks share the ``num_repos`` distinct fan-out reads). One injected slow repo
    (``asyncio.sleep(slow_delay)``) is the chaos knob.
    """
    repos = [f"/fake/repo{i}" for i in range(num_repos)]
    rows: list[dict] = []
    porc_records: dict[str, list[tuple[str, str]]] = {r: [] for r in repos}
    for i, repo in enumerate(repos):
        for j in range(tasks_per_repo):
            tid = f"t{i}_{j}"
            branch = f"agent/{tid}"
            wt = f"{repo}.wt/{tid}"
            rows.append(_task(tid, [(repo, branch, wt)]))
            porc_records[repo].append((wt, branch))
    porcelain = {repo: _porcelain(*porc_records[repo]) for repo in repos}
    store = FakeStore(rows)
    # The READ pool is sem=2 (GIT_READ_POOL_SIZE); mirror it so the slow-repo
    # injector throttles realistically. Slow repo = the first one.
    runner = FakeRunner(porcelain=porcelain, delays={repos[0]: slow_delay}, pool_size=2)
    return store, runner, repos


@pytest.mark.slow
@pytest.mark.parametrize(
    ("num_repos", "tasks_per_repo"),
    [(5, 1), (10, 2), (15, 3)],  # ≤15 DISTINCT repos × a varying tasks-per-repo axis
)
def test_fanout_p95_within_slo_up_to_15_repos(num_repos, tasks_per_repo):
    """AC4: p95 derive latency stays within the ≤3s soft SLO for ≤15 distinct repos."""
    assert num_repos <= _MAX_VALIDATED_REPOS, (
        "This test validates the SLO ONLY within the documented ≤15-repo envelope; "
        "the latency cliff beyond 15 repos is acknowledged, not guaranteed."
    )
    store, runner, _ = _build_fanout(num_repos, slow_delay=0.2, tasks_per_repo=tasks_per_repo)

    async def run():
        cache = _cache(runner, store)
        durations: list[float] = []
        for _ in range(15):
            start = time.perf_counter()
            await cache.refresh()
            durations.append(time.perf_counter() - start)
        return durations

    durations = sorted(asyncio.run(run()))
    p95 = durations[int(0.95 * (len(durations) - 1))]
    assert p95 <= _SLO_SECONDS, f"p95 {p95:.3f}s exceeded the ≤{_SLO_SECONDS}s SLO"


@pytest.mark.slow
def test_concurrent_readers_never_see_a_torn_snapshot():
    """AC1/AC4: readers of ``current`` during a refresh always get a whole, valid
    frozen snapshot — the swap is atomic, never a partial mutation."""
    store, runner, _ = _build_fanout(_MAX_VALIDATED_REPOS, slow_delay=0.05)

    async def run():
        cache = _cache(runner, store)
        stop = {"flag": False}
        seen: list[CacheSnapshot] = []

        async def reader():
            while not stop["flag"]:
                snap = cache.current
                # A torn read would surface as a non-snapshot or a mutated tuple;
                # assert the frozen contract holds on every read.
                assert isinstance(snap, CacheSnapshot)
                assert isinstance(snap.tasks, tuple)
                seen.append(snap)
                await asyncio.sleep(0)

        readers = [asyncio.create_task(reader()) for _ in range(4)]
        for _ in range(10):
            await cache.refresh()
        stop["flag"] = True
        await asyncio.gather(*readers)
        return seen

    seen = asyncio.run(run())
    assert seen  # readers actually ran
    # Every observed snapshot is a complete CacheSnapshot (no torn intermediate).
    assert all(isinstance(s, CacheSnapshot) for s in seen)
    # The readers must have interleaved with the swaps and observed MORE THAN ONE
    # distinct snapshot identity — otherwise the test proves nothing about reading
    # ACROSS an atomic swap (it would pass even if the cache never swapped). Each of
    # the 10 sequential refreshes produces a fresh frozen instance; readers yielding
    # via sleep(0) during the fan-out awaits sample several of them.
    assert len({id(s) for s in seen}) >= 2
