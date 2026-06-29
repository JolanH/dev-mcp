"""core.tasks.create — happy path + preflight rejection (AC 1–5).

Unit-tests the orchestrator directly with injected ``GitRunner``/``RepoLockRegistry``
and a tmp-file ``Store`` (no server, no port). Async is driven with ``asyncio.run``
(no pytest-asyncio); the deps are built inside each loop.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from dev_helper_mcp.config import branch_name_for, worktree_path_for
from dev_helper_mcp.core import tasks
from dev_helper_mcp.core.mutator import GitRepoMutator, _classify_add_failure
from dev_helper_mcp.errors import (
    ActiveTaskConflict,
    BaseRefNotFound,
    BranchExists,
    Internal,
    InvalidStatus,
    InvalidTaskName,
    NoDefaultBaseRef,
    NoDefaultRepo,
    NotAGitRepo,
    RollbackIncomplete,
    TaskNotFound,
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


# ── cwd-derived create_task defaults (resolve_default_repo / resolve_default_base_ref) ──


def test_resolve_default_repo_returns_toplevel(tmp_git_repo):
    # A nested subdir resolves up to the work-tree toplevel.
    sub = tmp_git_repo / "pkg"
    sub.mkdir()

    async def run():
        return await tasks.resolve_default_repo(runner=GitRunner(), cwd=str(sub))

    result = asyncio.run(run())
    assert os.path.realpath(result) == os.path.realpath(str(tmp_git_repo))


def test_resolve_default_repo_outside_a_repo_raises(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()

    async def run():
        with pytest.raises(NoDefaultRepo):
            await tasks.resolve_default_repo(runner=GitRunner(), cwd=str(outside))

    asyncio.run(run())


def test_resolve_default_base_ref_returns_current_branch(tmp_git_repo):
    async def run():
        return await tasks.resolve_default_base_ref(runner=GitRunner(), cwd=str(tmp_git_repo))

    assert asyncio.run(run()) == "main"


def test_resolve_default_base_ref_detached_head_raises(tmp_git_repo):
    # Detach HEAD onto the current commit so symbolic-ref has no branch to report.
    _git(tmp_git_repo, "checkout", "-q", "--detach", "HEAD")

    async def run():
        with pytest.raises(NoDefaultBaseRef):
            await tasks.resolve_default_base_ref(runner=GitRunner(), cwd=str(tmp_git_repo))

    asyncio.run(run())


def test_resolve_default_base_ref_outside_a_repo_raises(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()

    async def run():
        with pytest.raises(NoDefaultBaseRef):
            await tasks.resolve_default_base_ref(runner=GitRunner(), cwd=str(outside))

    asyncio.run(run())


# ══════════════════════════════════════════════════════════════════════════════
# Story 1.4 — cross-repo rollback (error-safe). Post-preflight `add` failures must
# tear down already-created worktrees in reverse order; a teardown that itself
# fails escalates to RollbackIncomplete. The RepoMutator seam makes the partial-
# failure matrix deterministic via in-process fault injection (no git corruption).
# ══════════════════════════════════════════════════════════════════════════════


def _canonical(repos) -> list[str]:
    """The sorted-abspath order the orchestrator locks/provisions on — the key the
    mutator sees as ``repo`` and the order compensation reverses."""
    return sorted(os.path.abspath(str(r)) for r in repos)


class FlakyMutator:
    """Wraps a real ``GitRepoMutator`` so non-targeted repos do REAL git (real
    worktrees/branches created and torn down → filesystem-assertable), while the
    targeted ``(repo, phase)`` raises a synthetic typed error in-process — never
    corrupting git state (project-context "Git safety in tests"). Keyed by absolute
    repo path to match the orchestrator's canonical order. Records call order so the
    reverse-teardown order is assertable."""

    def __init__(self, inner, *, fail_add_on: str | None = None, fail_remove_on: str | None = None):
        self._inner = inner
        self._fail_add_on = fail_add_on
        self._fail_remove_on = fail_remove_on
        self.add_calls: list[str] = []
        self.remove_calls: list[str] = []

    async def add(self, repo, branch, worktree_path, start_point) -> None:
        self.add_calls.append(repo)
        if self._fail_add_on is not None and repo == self._fail_add_on:
            # Synthetic post-preflight failure (a race surfacing as BranchExists).
            raise BranchExists("injected add failure", {"repo": repo, "branch": branch})
        await self._inner.add(repo, branch, worktree_path, start_point)

    async def remove(self, repo, branch, worktree_path) -> None:
        self.remove_calls.append(repo)
        if self._fail_remove_on is not None and repo == self._fail_remove_on:
            # Synthetic teardown failure → this repo stays orphaned (AC-3). The real
            # worktree/branch are left untouched on disk, exactly like a failed `git
            # worktree remove` would.
            raise Internal("injected remove failure", {"repo": repo})
        await self._inner.remove(repo, branch, worktree_path)


class SpyMutator:
    """Records calls and does nothing else — proves a preflight reject never enters
    provisioning (AC-2: "never started")."""

    def __init__(self):
        self.add_calls: list[str] = []
        self.remove_calls: list[str] = []

    async def add(self, repo, branch, worktree_path, start_point) -> None:
        self.add_calls.append(repo)

    async def remove(self, repo, branch, worktree_path) -> None:
        self.remove_calls.append(repo)


# ── AC-1 / AC-5: partial-failure matrix — fail add on repo i of N; clean rollback ──


@pytest.mark.parametrize("n", [1, 2, 3])
def test_add_failure_rolls_back_clean(n, tmp_path):
    """Fail the ``add`` of repo ``i`` (i ∈ {1, 2, N}) of ``N``: every repo ends with
    zero ``agent/<slug>`` branches, zero worktree dirs, zero DB rows, and teardown
    runs in reverse creation order. The triggering cause is the error raised."""
    fail_positions = sorted(p for p in {1, 2, n} if p <= n)  # 1-based, valid for N

    async def run():
        scenarios = []
        for pos in fail_positions:
            base = tmp_path / f"n{n}p{pos}"
            repos = [base / f"r{i}" for i in range(n)]
            for r in repos:
                _init_repo(r)
            canonical = _canonical(repos)
            fail_repo = canonical[pos - 1]
            slug = f"matrix-{n}-{pos}"
            store = await Store.open(base / "state.db")
            try:
                runner = GitRunner()
                flaky = FlakyMutator(GitRepoMutator(runner), fail_add_on=fail_repo)
                with pytest.raises(BranchExists):
                    await tasks.create(
                        slug,
                        "x",
                        [str(r) for r in repos],
                        runner=runner,
                        locks=RepoLockRegistry(),
                        store=store,
                        mutator=flaky,
                    )
                row = await store.get_task(slug)
            finally:
                await store.close()
            scenarios.append((repos, canonical, fail_repo, slug, flaky, row))
        return scenarios

    for repos, canonical, fail_repo, slug, flaky, row in asyncio.run(run()):
        # No DB row — the call looks like it never happened.
        assert row is None, f"{slug}: unexpected task row"
        # No residue in ANY repo.
        for r in repos:
            assert not _branch_exists(r, f"agent/{slug}"), f"{slug}: branch leaked in {r}"
            assert not os.path.isdir(str(worktree_path_for(r, slug))), f"{slug}: wt leaked in {r}"
        # Teardown is the reverse of creation order; only repos provisioned BEFORE the
        # failing one are torn down (the failing repo was never appended).
        fail_idx = canonical.index(fail_repo)
        assert flaky.remove_calls == list(reversed(canonical[:fail_idx])), f"{slug}: order"


# ── AC-3: compensation itself fails → RollbackIncomplete, original cause preserved ──


def test_compensation_failure_raises_rollback_incomplete(tmp_path):
    """Fail ``add`` at C (so A and B are provisioned) AND fail the ``remove`` of A:
    rollback removes B (reverse order) then fails on A → RollbackIncomplete naming
    exactly A as orphaned, the triggering cause preserved, no DB row, B left clean."""
    repos = [tmp_path / "ra", tmp_path / "rb", tmp_path / "rc"]
    for r in repos:
        _init_repo(r)
    canonical = _canonical(repos)
    fail_add = canonical[2]  # C — last → A and B get provisioned first
    fail_remove = canonical[0]  # A — its teardown fails → orphaned
    torn_down_ok = canonical[1]  # B — torn down successfully before A is attempted

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            runner = GitRunner()
            flaky = FlakyMutator(
                GitRepoMutator(runner), fail_add_on=fail_add, fail_remove_on=fail_remove
            )
            with pytest.raises(RollbackIncomplete) as ei:
                await tasks.create(
                    "rbi",
                    "x",
                    [str(r) for r in repos],
                    runner=runner,
                    locks=RepoLockRegistry(),
                    store=store,
                    mutator=flaky,
                )
            row = await store.get_task("rbi")
            return ei.value, row, flaky
        finally:
            await store.close()

    err, row, flaky = asyncio.run(run())

    # No rows persisted even on a failed compensation.
    assert row is None
    details = err.details
    # Exactly the repo whose teardown failed is reported orphaned (snake_case keys).
    assert details["orphaned_repos"] == [fail_remove]
    assert torn_down_ok not in details["orphaned_repos"]
    # The triggering cause is preserved (not swallowed) — both in details and chained.
    assert details["original_cause"]["code"] == "BranchExists"
    assert isinstance(err.__cause__, BranchExists)
    # Reverse-order teardown attempted B then A.
    assert flaky.remove_calls == [torn_down_ok, fail_remove]
    # B was really torn down (no residue); A is the genuine orphan (residue remains).
    assert not _branch_exists(Path(torn_down_ok), "agent/rbi")
    assert not os.path.isdir(str(worktree_path_for(Path(torn_down_ok), "rbi")))
    assert _branch_exists(Path(fail_remove), "agent/rbi")
    assert os.path.isdir(str(worktree_path_for(Path(fail_remove), "rbi")))


# ── AC-4: a clean rollback leaves no residue — same-name retry succeeds ──


def test_clean_rollback_allows_retry(tmp_path):
    repos = [tmp_path / "a", tmp_path / "b"]
    for r in repos:
        _init_repo(r)
    canonical = _canonical(repos)

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            runner = GitRunner()
            locks = RepoLockRegistry()
            # First attempt rolls back cleanly (fail add on the 2nd-provisioned repo).
            flaky = FlakyMutator(GitRepoMutator(runner), fail_add_on=canonical[1])
            with pytest.raises(BranchExists):
                await tasks.create(
                    "retry",
                    "first",
                    [str(r) for r in repos],
                    runner=runner,
                    locks=locks,
                    store=store,
                    mutator=flaky,
                )
            # Retry with the same name + repos using the REAL (default) mutator.
            data = await tasks.create(
                "retry",
                "second",
                [str(r) for r in repos],
                runner=runner,
                locks=locks,
                store=store,
            )
            return data, await store.get_task("retry"), await store.count_worktrees("retry")
        finally:
            await store.close()

    data, row, n_wt = asyncio.run(run())
    assert len(data["worktrees"]) == 2
    assert row is not None and row["status"] == "running"
    assert row["description"] == "second"
    assert n_wt == 2
    for r in repos:
        assert _branch_exists(r, "agent/retry")
        assert os.path.isdir(str(worktree_path_for(r, "retry")))


# ── AC-2 preserved: a preflight reject never enters provisioning (never started) ──


def test_preflight_reject_never_calls_mutator(tmp_path):
    repos = [tmp_path / "a", tmp_path / "b"]
    for r in repos:
        _init_repo(r)
    # Pre-create the colliding branch in the SECOND repo so preflight rejects.
    _git(repos[1], "branch", "agent/dup")

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            spy = SpyMutator()
            with pytest.raises(BranchExists):
                await tasks.create(
                    "dup",
                    "x",
                    [str(r) for r in repos],
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                    mutator=spy,
                )
            return spy, await store.get_task("dup")
        finally:
            await store.close()

    spy, row = asyncio.run(run())
    # The mutator was never touched — provisioning never started; no rows.
    assert spy.add_calls == []
    assert spy.remove_calls == []
    assert row is None


# ── Task 4: GitRepoMutator typed classification of a REAL `git worktree add` failure ──


def test_git_mutator_add_classifies_branch_exists(tmp_git_repo):
    # Pre-create the branch so a real `worktree add -b` fails on the branch.
    _git(tmp_git_repo, "branch", "agent/clash")

    async def run():
        m = GitRepoMutator(GitRunner())
        with pytest.raises(BranchExists):
            await m.add(
                os.path.abspath(str(tmp_git_repo)),
                "agent/clash",
                str(worktree_path_for(tmp_git_repo, "clash")),
                "HEAD",
            )

    asyncio.run(run())


def test_git_mutator_add_classifies_path_in_use(tmp_git_repo):
    # Pre-create a non-empty target dir so a real `worktree add` fails on the path.
    wt = worktree_path_for(tmp_git_repo, "occ")
    wt.mkdir(parents=True)
    (wt / "f.txt").write_text("x\n")

    async def run():
        m = GitRepoMutator(GitRunner())
        with pytest.raises(WorktreePathInUse):
            await m.add(
                os.path.abspath(str(tmp_git_repo)),
                "agent/occ",
                str(wt),
                "HEAD",
            )

    asyncio.run(run())


def test_git_mutator_remove_raises_on_worktree_failure(tmp_git_repo):
    # No such worktree → real `git worktree remove` returns non-zero → typed Internal
    # naming the FIRST teardown step (worktree remove). This is how the orchestrator
    # detects a failed teardown (AC-3); assert the specific op so a regression that
    # swaps/loses the worktree-remove path is caught (not just "some Internal").
    async def run():
        m = GitRepoMutator(GitRunner())
        with pytest.raises(Internal) as ei:
            await m.remove(
                os.path.abspath(str(tmp_git_repo)),
                "agent/nope",
                str(worktree_path_for(tmp_git_repo, "nope")),
            )
        return ei.value

    err = asyncio.run(run())
    assert "git worktree remove failed" in err.message
    assert err.details["worktree_path"].endswith("nope")


def test_git_mutator_remove_raises_on_branch_failure(tmp_git_repo):
    # Worktree removal succeeds but `branch -D` fails (the branch does not exist) →
    # Internal naming the SECOND teardown step. Proves the two failure paths are
    # distinct and not swapped, and that the worktree was really removed first.
    wt = worktree_path_for(tmp_git_repo, "real")
    _git(tmp_git_repo, "worktree", "add", "-b", "agent/real", str(wt), "HEAD", "--")

    async def run():
        m = GitRepoMutator(GitRunner())
        with pytest.raises(Internal) as ei:
            # Correct worktree path (removes cleanly) but a branch that doesn't exist.
            await m.remove(
                os.path.abspath(str(tmp_git_repo)),
                "agent/does-not-exist",
                str(wt),
            )
        return ei.value

    err = asyncio.run(run())
    assert "git branch -D failed" in err.message
    assert err.details["branch"] == "agent/does-not-exist"
    # The worktree WAS removed (first step succeeded before the branch step failed).
    assert not os.path.isdir(str(wt))


# ── Task 4 (review): _classify_add_failure precedence + Internal fallback, unit-level ──


def test_classify_add_failure_precedence_and_fallback():
    """Direct unit test of the stderr classifier — the most regression-prone logic
    (substring matching, branch-before-path precedence, Internal fallback). The
    branch-collision message contains BOTH "a branch named" AND "already exists";
    branch precedence must win so it maps to BranchExists, not WorktreePathInUse."""
    branch_err = _classify_add_failure(
        "/r", "agent/x", "/wt", b"fatal: a branch named 'agent/x' already exists"
    )
    assert isinstance(branch_err, BranchExists)

    checked_out = _classify_add_failure(
        "/r", "agent/x", "/wt", b"fatal: 'agent/x' is already checked out at '/other'"
    )
    assert isinstance(checked_out, BranchExists)

    path_err = _classify_add_failure("/r", "agent/x", "/wt", b"fatal: '/wt' already exists")
    assert isinstance(path_err, WorktreePathInUse)

    # Unrecognized (or locale-translated) stderr → Internal carrying trimmed stderr.
    fallback = _classify_add_failure("/r", "agent/x", "/wt", b"  fatal: some other error\n")
    assert isinstance(fallback, Internal)
    assert fallback.details["stderr"] == "fatal: some other error"


# ── Review patch: a persist failure AFTER full provisioning must also roll back ──


def test_persist_failure_rolls_back_worktrees(tmp_path):
    """The persist call lives inside the compensation try: if it fails after every
    worktree is provisioned (e.g. a TOCTOU ActiveTaskConflict), all worktrees +
    branches are torn down in reverse order, no rows persist, and the original
    persist cause surfaces — the all-or-nothing guarantee holds for the persist
    window too, not just `add` failures."""
    repos = [tmp_path / "a", tmp_path / "b"]
    for r in repos:
        _init_repo(r)
    canonical = _canonical(repos)

    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            runner = GitRunner()
            # Real adds + real removes (no injected mutator fault) — only persist fails.
            flaky = FlakyMutator(GitRepoMutator(runner))

            async def boom(**kwargs):
                raise ActiveTaskConflict("injected persist failure", {"task_id": kwargs["task_id"]})

            store.persist_created_task = boom
            with pytest.raises(ActiveTaskConflict):
                await tasks.create(
                    "persistfail",
                    "x",
                    [str(r) for r in repos],
                    runner=runner,
                    locks=RepoLockRegistry(),
                    store=store,
                    mutator=flaky,
                )
            row = await store.get_task("persistfail")
            return row, flaky
        finally:
            await store.close()

    row, flaky = asyncio.run(run())
    # No rows — the call looks like it never happened.
    assert row is None
    # Every provisioned worktree was torn down, in reverse creation order.
    assert flaky.remove_calls == list(reversed(canonical))
    for r in repos:
        assert not _branch_exists(r, "agent/persistfail")
        assert not os.path.isdir(str(worktree_path_for(r, "persistfail")))


# ══════════════════════════════════════════════════════════════════════════════
# Story 1.6 — update_task (status lifecycle + 4×4 transition matrix) and list_tasks.
# Pure status logic seeds task rows directly via the Store (git-free); only the AC4
# slug-reuse regressions and list_tasks multi-repo fixtures spawn real git
# (tmp_git_repo/_init_repo), per the git-safety rule.
# ══════════════════════════════════════════════════════════════════════════════

_TS = "2026-06-22T10:00:00Z"
_ALL_STATUSES = ("running", "blocked", "review", "done")


def test_update_task_status_and_description(tmp_path):
    """AC1: status+description updated, updated_at bumped, created_at preserved."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            await store.add_task("t1", "first", "running", _TS, _TS)
            data = await tasks.update_task("t1", status="review", description="second", store=store)
            return data, await store.get_task("t1")
        finally:
            await store.close()

    data, row = asyncio.run(run())
    assert data["task_id"] == "t1"
    assert data["status"] == "review"
    assert data["description"] == "second"
    assert data["created_at"] == _TS
    assert data["updated_at"] > _TS  # advanced (now_iso() is "today")
    # Persisted exactly as returned; created_at untouched.
    assert row["status"] == "review"
    assert row["description"] == "second"
    assert row["created_at"] == _TS
    assert row["updated_at"] == data["updated_at"]


def test_update_task_out_of_set_status_rejected(tmp_path):
    """AC1: a status outside the four-state set → InvalidStatus(reason=not_in_set), no change."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            await store.add_task("t1", "d", "running", _TS, _TS)
            with pytest.raises(InvalidStatus) as ei:
                await tasks.update_task("t1", status="merged", store=store)
            return ei.value, await store.get_task("t1")
        finally:
            await store.close()

    err, row = asyncio.run(run())
    assert err.details["reason"] == "not_in_set"
    assert row["status"] == "running"  # unchanged
    assert row["updated_at"] == _TS  # not bumped


def test_update_task_unknown_task_not_found(tmp_path):
    """AC3: a non-existent task_id → TaskNotFound."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(TaskNotFound):
                await tasks.update_task("ghost", status="done", store=store)
        finally:
            await store.close()

    asyncio.run(run())


def test_update_task_noop_returns_unchanged_without_bump(tmp_path):
    """Review patch: both fields None → return the task unchanged, NO DB write, NO
    updated_at bump (a no-op must not silently advance the timestamp)."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            await store.add_task("t1", "d", "running", _TS, _TS)
            data = await tasks.update_task("t1", store=store)
            return data, await store.get_task("t1")
        finally:
            await store.close()

    data, row = asyncio.run(run())
    assert data["status"] == "running"
    assert data["description"] == "d"
    assert data["created_at"] == _TS
    assert data["updated_at"] == _TS  # not bumped
    assert row["updated_at"] == _TS  # no DB write happened


def test_update_task_phantom_success_guarded(tmp_path):
    """Review patch: if the row vanishes between the get_task precheck and the write
    (store.update_task reports no match), core raises TaskNotFound instead of
    fabricating success from the stale snapshot."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            await store.add_task("t1", "d", "running", _TS, _TS)

            async def no_match(*args, **kwargs):  # simulate the TOCTOU delete
                return False

            store.update_task = no_match
            with pytest.raises(TaskNotFound):
                await tasks.update_task("t1", status="review", store=store)
        finally:
            await store.close()

    asyncio.run(run())


@pytest.mark.parametrize("dst", _ALL_STATUSES)
@pytest.mark.parametrize("src", _ALL_STATUSES)
def test_transition_matrix(src, dst, tmp_path):
    """AC2: the full 4×4 matrix — 12 non-``done``-source pairs pass, the 4 ``done``-source
    pairs reject with InvalidStatus(reason=illegal_transition). ``done`` is terminal,
    including ``done → done``. Git-free: the src status is seeded directly via the Store.
    """

    async def run():
        store = await _make_store(tmp_path)
        try:
            await store.add_task("t", "d", src, _TS, _TS)
            if src == "done":
                with pytest.raises(InvalidStatus) as ei:
                    await tasks.update_task("t", status=dst, store=store)
                return "reject", ei.value.details["reason"], await store.get_task("t")
            data = await tasks.update_task("t", status=dst, store=store)
            return "ok", data, await store.get_task("t")
        finally:
            await store.close()

    kind, payload, row = asyncio.run(run())
    if src == "done":
        assert kind == "reject"
        assert payload == "illegal_transition"
        assert row["status"] == "done"  # terminal — unchanged
    else:
        assert kind == "ok"
        assert payload["status"] == dst
        assert row["status"] == dst


def test_update_task_done_keeps_row_and_links(tmp_git_repo, tmp_path):
    """AC4: ``done`` sets status ONLY — the task row + its worktree links are NOT deleted
    (that DELETE is remove_worktree's last-worktree path, a distinct 'closed' semantics)."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "keep",
                "d",
                [str(tmp_git_repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            await tasks.update_task("keep", status="done", store=store)
            return await store.get_task("keep"), await store.count_worktrees("keep")
        finally:
            await store.close()

    row, n_wt = asyncio.run(run())
    assert row is not None and row["status"] == "done"
    assert n_wt == 1  # links preserved on done


def test_update_task_done_releases_slug_review_blocks(tmp_git_repo, tmp_path):
    """AC4 highest-risk seam (epics.md:226): an ACTIVE status (review) blocks re-creating
    the slug (ActiveTaskConflict), while ``done`` releases it so a new create_task wins."""

    async def run():
        store = await _make_store(tmp_path)
        try:
            runner = GitRunner()
            locks = RepoLockRegistry()
            first = await tasks.create(
                "reuse", "first", [str(tmp_git_repo)], runner=runner, locks=locks, store=store
            )
            # review is active → re-creating the slug must reject.
            await tasks.update_task("reuse", status="review", store=store)
            with pytest.raises(ActiveTaskConflict):
                await tasks.create(
                    "reuse", "again", [str(tmp_git_repo)], runner=runner, locks=locks, store=store
                )
            # done releases the slug; tear down git residue so preflight passes again.
            await tasks.update_task("reuse", status="done", store=store)
            _git(
                tmp_git_repo,
                "worktree",
                "remove",
                "--force",
                first["worktrees"][0]["worktree_path"],
            )
            _git(tmp_git_repo, "branch", "-D", "agent/reuse")
            second = await tasks.create(
                "reuse", "third", [str(tmp_git_repo)], runner=runner, locks=locks, store=store
            )
            return second, await store.get_task("reuse")
        finally:
            await store.close()

    second, row = asyncio.run(run())
    assert second["task_id"] == "reuse"
    assert row["status"] == "running"  # re-tasked from done
    assert row["description"] == "third"


def test_list_tasks_links_and_filters(tmp_path):
    """AC5: list_tasks returns full task rows with nested per-repo links; status/repo
    filters narrow correctly; an empty-string repo means 'no filter' (1.5 fix)."""
    repos = [tmp_path / "a", tmp_path / "b"]
    for r in repos:
        _init_repo(r)

    async def run():
        store = await _make_store(tmp_path)
        try:
            runner = GitRunner()
            locks = RepoLockRegistry()
            await tasks.create(
                "alpha",
                "d",
                [str(repos[0]), str(repos[1])],
                runner=runner,
                locks=locks,
                store=store,
            )
            await tasks.create(
                "beta", "d", [str(repos[0])], runner=runner, locks=locks, store=store
            )
            return (
                await tasks.list_tasks(store=store),
                await tasks.list_tasks(repo=str(repos[1]), store=store),
                await tasks.list_tasks(repo="", store=store),
                await tasks.list_tasks(status="done", store=store),
            )
        finally:
            await store.close()

    all_tasks, by_repo_b, empty_filter, done_only = asyncio.run(run())
    abspaths = sorted(os.path.abspath(str(r)) for r in repos)
    assert [t["task_id"] for t in all_tasks] == ["alpha", "beta"]
    alpha = all_tasks[0]
    assert set(alpha) == {
        "task_id",
        "description",
        "status",
        "created_at",
        "updated_at",
        "worktrees",
    }
    assert [w["repo_path"] for w in alpha["worktrees"]] == abspaths  # spans both, sorted
    assert alpha["worktrees"][0]["branch"] == "agent/alpha"
    # repo filter: only the task touching repo b, links limited to it.
    assert [t["task_id"] for t in by_repo_b] == ["alpha"]
    assert [w["repo_path"] for w in by_repo_b[0]["worktrees"]] == [os.path.abspath(str(repos[1]))]
    # empty-string repo == no filter.
    assert {t["task_id"] for t in empty_filter} == {"alpha", "beta"}
    # nothing is done yet.
    assert done_only == []
