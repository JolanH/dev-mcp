"""core.worktrees — live list (AC 1) and guarded remove (AC 2–5).

Unit-tests the core directly with injected ``GitRunner``/``RepoLockRegistry`` and
a tmp-file ``Store`` (no server, no port). Real destructive git runs ONLY against
``tmp_git_repo``/``tmp_path`` repos (HARD git-safety rule); every probe passes
``-C <tmp_repo>`` and a ``GIT_*``-stripped ``env=`` (gate-enforced).
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from dev_helper_mcp.config import worktree_path_for
from dev_helper_mcp.core import tasks, worktrees
from dev_helper_mcp.errors import DirtyWorktree, GitTimeout, TaskNotFound, UnmergedBranch
from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitRunner
from dev_helper_mcp.store import Store

# GIT_*-stripped env (see tmp_git_repo in conftest): under the pre-commit hook git
# exports GIT_DIR / GIT_INDEX_FILE etc., which would redirect these probe calls at
# the outer (project) repo instead of the per-test tmp repo.
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


def _git_rc(repo, *args: str) -> int:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        env=_ENV,
    ).returncode


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


def _branch_exists(repo, branch: str) -> bool:
    return _git_rc(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") == 0


def _worktree_listed(repo, path: str) -> bool:
    out = _git(repo, "worktree", "list", "--porcelain")
    return f"worktree {path}" in out


def _make_store(tmp_path):
    return Store.open(tmp_path / "state.db")


# ════════════════════════════ AC 1 — list_worktrees ════════════════════════════


def test_list_joins_live_git_with_store_across_repos(tmp_path):
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    _init_repo(repo_a)
    _init_repo(repo_b)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "feat",
                "d",
                [str(repo_a), str(repo_b)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            return await worktrees.list_worktrees(
                repo=None, task_id=None, runner=GitRunner(), store=store
            )
        finally:
            await store.close()

    result = asyncio.run(run())
    assert len(result) == 2
    by_repo = {e["repo_path"]: e for e in result}
    assert set(by_repo) == {str(repo_a), str(repo_b)}
    for repo in (repo_a, repo_b):
        e = by_repo[str(repo)]
        assert e["task_id"] == "feat"
        assert e["branch"] == "agent/feat"
        assert e["status"] == "running"
        assert e["worktree_path"] == str(worktree_path_for(repo, "feat"))
        assert e["orphaned"] is False


def test_list_filters_by_repo_and_task(tmp_path):
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    _init_repo(repo_a)
    _init_repo(repo_b)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "alpha",
                "d",
                [str(repo_a), str(repo_b)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            await tasks.create(
                "beta",
                "d",
                [str(repo_a)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            by_repo = await worktrees.list_worktrees(
                repo=str(repo_b), task_id=None, runner=GitRunner(), store=store
            )
            by_task = await worktrees.list_worktrees(
                repo=None, task_id="beta", runner=GitRunner(), store=store
            )
            return by_repo, by_task
        finally:
            await store.close()

    by_repo, by_task = asyncio.run(run())
    # repo_b only hosts "alpha".
    assert len(by_repo) == 1
    assert by_repo[0]["repo_path"] == str(repo_b)
    assert by_repo[0]["task_id"] == "alpha"
    # task "beta" only in repo_a.
    assert len(by_task) == 1
    assert by_task[0]["task_id"] == "beta"
    assert by_task[0]["repo_path"] == str(repo_a)


def test_list_repo_filter_accepts_relative_path(tmp_path, monkeypatch):
    repo = tmp_path / "rel"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            monkeypatch.chdir(tmp_path)
            # A relative repo path must canonicalize to the stored abspath.
            return await worktrees.list_worktrees(
                repo="rel", task_id=None, runner=GitRunner(), store=store
            )
        finally:
            await store.close()

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0]["repo_path"] == str(repo)


def test_orphaned_link_is_flagged_not_dropped(tmp_path):
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            created = await tasks.create(
                "gone",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            wt = created["worktrees"][0]["worktree_path"]
            # Out-of-band teardown: the live branch disappears but the DB link stays.
            _git(repo, "worktree", "remove", "--force", wt)
            _git(repo, "branch", "-D", "agent/gone")
            result = await worktrees.list_worktrees(
                repo=None, task_id=None, runner=GitRunner(), store=store
            )
            return result
        finally:
            await store.close()

    result = asyncio.run(run())
    # The link is RETURNED (derive-on-read: never auto-deleted) but flagged orphaned.
    assert len(result) == 1
    assert result[0]["task_id"] == "gone"
    assert result[0]["orphaned"] is True


def test_list_empty_when_no_tasks(tmp_path):
    async def run():
        store = await _make_store(tmp_path)
        try:
            return await worktrees.list_worktrees(
                repo=None, task_id=None, runner=GitRunner(), store=store
            )
        finally:
            await store.close()

    assert asyncio.run(run()) == []


# ════════════════════════ AC 2/3/4/5 — remove_worktree ════════════════════════


def test_remove_one_leaves_other_repo_untouched(tmp_path):
    """AC2: remove one repo's worktree; the task's other repo is unaffected."""
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    _init_repo(repo_a)
    _init_repo(repo_b)

    async def run():
        store = await _make_store(tmp_path)
        try:
            created = await tasks.create(
                "feat",
                "d",
                [str(repo_a), str(repo_b)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            data = await worktrees.remove_worktree(
                "feat",
                str(repo_a),
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            links_after = await store.list_worktree_links()
            return created, data, links_after
        finally:
            await store.close()

    created, data, links_after = asyncio.run(run())
    wt_a = str(worktree_path_for(repo_a, "feat"))
    wt_b = str(worktree_path_for(repo_b, "feat"))
    # The removed repo's worktree is gone on disk + de-tracked in live git.
    assert not os.path.isdir(wt_a)
    assert not _worktree_listed(repo_a, wt_a)
    # Its row dropped; the sibling (repo_b) row + worktree intact.
    assert data["task_closed"] is False
    assert {link["repo_path"] for link in links_after} == {str(repo_b)}
    assert os.path.isdir(wt_b)
    assert _worktree_listed(repo_b, wt_b)


def test_dirty_worktree_blocks_without_force_then_force_removes(tmp_path):
    """AC3: dirty worktree → DirtyWorktree, nothing changes; force=true removes it."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            created = await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            wt = created["worktrees"][0]["worktree_path"]
            # Dirty it: an uncommitted untracked file blocks the safe remove.
            (Path(wt) / "dirty.txt").write_text("uncommitted\n")

            with pytest.raises(DirtyWorktree):
                await worktrees.remove_worktree(
                    "feat",
                    str(repo),
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
            # Nothing changed: row + worktree + branch all still present.
            blocked_links = await store.list_worktree_links()
            still_there = os.path.isdir(wt) and _worktree_listed(repo, wt)

            forced = await worktrees.remove_worktree(
                "feat",
                str(repo),
                force=True,
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            after_links = await store.list_worktree_links()
            return wt, blocked_links, still_there, forced, after_links
        finally:
            await store.close()

    wt, blocked_links, still_there, forced, after_links = asyncio.run(run())
    assert len(blocked_links) == 1  # guard left the row intact (AC3 "nothing changes")
    assert still_there is True
    assert _branch_exists(repo, "agent/feat")  # branch untouched on the guard
    # force=true removed it; last worktree → task closed (AC5).
    assert not os.path.isdir(wt)
    assert forced["task_closed"] is True
    assert after_links == []


def test_unmerged_branch_blocks_then_force_deletes(tmp_path):
    """AC4: delete_branch on an unmerged branch → UnmergedBranch w/ count first."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            created = await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            wt = created["worktrees"][0]["worktree_path"]
            # Commit on the agent branch (in its worktree) so it is NOT merged.
            (Path(wt) / "work.txt").write_text("unmerged\n")
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "unmerged work")

            with pytest.raises(UnmergedBranch) as ei:
                await worktrees.remove_worktree(
                    "feat",
                    str(repo),
                    delete_branch=True,
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
            details = ei.value.details
            branch_present_after_guard = _branch_exists(repo, "agent/feat")

            forced = await worktrees.remove_worktree(
                "feat",
                str(repo),
                delete_branch=True,
                force_unmerged_branch=True,
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            branch_present_after_force = _branch_exists(repo, "agent/feat")
            return details, branch_present_after_guard, forced, branch_present_after_force
        finally:
            await store.close()

    details, after_guard, forced, after_force = asyncio.run(run())
    # The unmerged-commit count is surfaced FIRST in details.
    assert details["unmerged_commits"] == 1
    assert details["branch"] == "agent/feat"
    # The worktree was removed before the branch step (documented in details).
    assert details["worktree_removed"] is True
    # The branch survives the guard but force_unmerged_branch deletes it.
    assert after_guard is True
    assert forced["branch_deleted"] is True
    assert after_force is False


def test_remove_last_worktree_closes_task(tmp_path):
    """AC5: removing a task's last worktree deletes the task record."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "solo",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            data = await worktrees.remove_worktree(
                "solo",
                str(repo),
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            task_after = await store.get_task("solo")
            return data, task_after
        finally:
            await store.close()

    data, task_after = asyncio.run(run())
    assert data["task_closed"] is True
    assert task_after is None  # the task row is gone (closed/detached)


def test_remove_unknown_task_raises_task_not_found(tmp_path):
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            with pytest.raises(TaskNotFound):
                await worktrees.remove_worktree(
                    "nope",
                    str(repo),
                    runner=GitRunner(),
                    locks=RepoLockRegistry(),
                    store=store,
                )
        finally:
            await store.close()

    asyncio.run(run())


# ════════════════════ Code-review patches (2026-06-23) ════════════════════


def test_remove_reports_worktree_already_gone_on_orphan_cleanup(tmp_path):
    """Patch: removing an orphaned link (worktree gone out-of-band) flags it."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            # Baseline: a normal live removal reports the flag False.
            await tasks.create(
                "live",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            live = await worktrees.remove_worktree(
                "live",
                str(repo),
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )

            # Orphan: create then remove the worktree out-of-band, link stays.
            created = await tasks.create(
                "orphan",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            _git(repo, "worktree", "remove", "--force", created["worktrees"][0]["worktree_path"])
            orphan = await worktrees.remove_worktree(
                "orphan",
                str(repo),
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            links_after = await store.list_worktree_links()
            return live, orphan, links_after
        finally:
            await store.close()

    live, orphan, links_after = asyncio.run(run())
    assert live["worktree_already_gone"] is False
    assert orphan["worktree_already_gone"] is True  # distinguishes orphan cleanup
    assert orphan["task_closed"] is True
    assert links_after == []  # the orphaned link was cleaned, not wedged


def test_remove_with_delete_branch_when_branch_already_gone_is_idempotent(tmp_path):
    """Patch: branch already deleted → branch -d 'not found' is NOT a hard error."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            # Delete the branch out-of-band (worktree first, then the branch), so the
            # remove's branch step hits "branch not found".
            _git(repo, "worktree", "remove", "--force", str(worktree_path_for(repo, "feat")))
            _git(repo, "branch", "-D", "agent/feat")
            # delete_branch=True must NOT wedge on the already-gone branch.
            data = await worktrees.remove_worktree(
                "feat",
                str(repo),
                delete_branch=True,
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            links_after = await store.list_worktree_links()
            return data, links_after
        finally:
            await store.close()

    data, links_after = asyncio.run(run())
    assert data["branch_deleted"] is True
    assert data["worktree_already_gone"] is True
    assert data["task_closed"] is True
    assert links_after == []  # link dropped, not permanently wedged as Internal


def test_list_empty_string_repo_is_treated_as_no_filter(tmp_path):
    """Patch: repo="" must not abspath("")→cwd and silently match nothing."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            return await worktrees.list_worktrees(
                repo="", task_id=None, runner=GitRunner(), store=store
            )
        finally:
            await store.close()

    result = asyncio.run(run())
    assert len(result) == 1  # "" == no filter, not a cwd filter matching nothing
    assert result[0]["task_id"] == "feat"


class _RaisingRunner:
    """A runner whose git invocations always raise GitTimeout (test double)."""

    async def run_git(self, repo, args, *, pool):
        raise GitTimeout("simulated hang", {"repo": repo})


def test_list_degrades_when_run_git_raises(tmp_path):
    """Patch: a raised GitTimeout from one repo must not abort the whole list."""
    repo = tmp_path / "a"
    _init_repo(repo)

    async def run():
        store = await _make_store(tmp_path)
        try:
            await tasks.create(
                "feat",
                "d",
                [str(repo)],
                runner=GitRunner(),
                locks=RepoLockRegistry(),
                store=store,
            )
            # The live-derive runner raises; list must degrade (links read orphaned),
            # never propagate the GitTimeout.
            return await worktrees.list_worktrees(
                repo=None, task_id=None, runner=_RaisingRunner(), store=store
            )
        finally:
            await store.close()

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0]["task_id"] == "feat"
    assert result[0]["orphaned"] is True  # degraded to "no live worktrees"
