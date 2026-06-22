"""Store bootstrap: two tables, FK cascade, WAL, version-check refusal (AC 4)."""

import asyncio

import aiosqlite
import pytest

from dev_helper_mcp.errors import ActiveTaskConflict, DevHelperError
from dev_helper_mcp.store import Store

_TS = "2026-06-22T10:00:00Z"


def test_bootstrap_creates_both_tables(tmp_path):
    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            return await store.table_names()
        finally:
            await store.close()

    assert {"task", "task_worktree"} <= set(asyncio.run(main()))


def test_fk_cascade_deletes_worktree_links(tmp_path):
    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            await store.add_task("t1", "desc", "running", _TS, _TS)
            await store.add_worktree("t1", "/repo/a", "agent/t1", "/repo/a.worktrees/t1")
            await store.add_worktree("t1", "/repo/b", "agent/t1", "/repo/b.worktrees/t1")
            before = await store.count_worktrees("t1")
            await store.delete_task("t1")
            after = await store.count_worktrees("t1")
            return before, after
        finally:
            await store.close()

    before, after = asyncio.run(main())
    assert before == 2
    assert after == 0  # ON DELETE CASCADE fired → foreign_keys=ON is live


def test_status_check_constraint_rejects_bad_status(tmp_path):
    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            with pytest.raises(aiosqlite.IntegrityError):
                await store.add_task("t1", "desc", "bogus", _TS, _TS)
        finally:
            await store.close()

    asyncio.run(main())


def test_journal_mode_is_wal_on_file_db(tmp_path):
    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            return await store.journal_mode()
        finally:
            await store.close()

    assert asyncio.run(main()) == "wal"


def test_reopen_is_idempotent(tmp_path):
    db = tmp_path / "state.db"

    async def main():
        s1 = await Store.open(db)
        await s1.close()
        s2 = await Store.open(db)  # must not error on re-open
        try:
            return await s2.table_names()
        finally:
            await s2.close()

    assert {"task", "task_worktree"} <= set(asyncio.run(main()))


def test_persist_created_task_is_atomic(tmp_path):
    """The single-transaction persist writes the task row + N worktree rows."""

    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            await store.persist_created_task(
                task_id="t1",
                description="desc",
                status="running",
                created_at=_TS,
                updated_at=_TS,
                worktrees=[
                    ("/repo/a", "agent/t1", "/repo/a.worktrees/t1"),
                    ("/repo/b", "agent/t1", "/repo/b.worktrees/t1"),
                ],
            )
            return await store.get_task("t1"), await store.count_worktrees("t1")
        finally:
            await store.close()

    row, n_wt = asyncio.run(main())
    assert row["status"] == "running"
    assert n_wt == 2


def test_retask_done_replaces_and_preserves_created_at(tmp_path):
    """Re-tasking a ``done`` slug purges old worktree rows + keeps ``created_at``."""

    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            await store.persist_created_task(
                task_id="t1",
                description="first",
                status="running",
                created_at="2026-06-22T10:00:00Z",
                updated_at="2026-06-22T10:00:00Z",
                worktrees=[
                    ("/repo/a", "agent/t1", "/repo/a.worktrees/t1"),
                    ("/repo/b", "agent/t1", "/repo/b.worktrees/t1"),
                ],
            )
            await store._conn.execute("UPDATE task SET status='done' WHERE task_id='t1'")
            await store._conn.commit()
            # Re-task: fewer repos this time → stale rows must be purged.
            await store.persist_created_task(
                task_id="t1",
                description="second",
                status="running",
                created_at="2026-06-22T10:00:00Z",  # caller preserves original
                updated_at="2026-06-22T12:00:00Z",
                worktrees=[("/repo/a", "agent/t1", "/repo/a.worktrees/t1")],
            )
            return await store.get_task("t1"), await store.count_worktrees("t1")
        finally:
            await store.close()

    row, n_wt = asyncio.run(main())
    assert row["description"] == "second"
    assert row["status"] == "running"
    assert row["created_at"] == "2026-06-22T10:00:00Z"  # preserved
    assert row["updated_at"] == "2026-06-22T12:00:00Z"  # advanced
    assert n_wt == 1  # the stale /repo/b row was cascade-purged


def test_persist_active_slug_clash_maps_to_active_task_conflict(tmp_path):
    """A live (non-done) ``task_id`` clash → ``ActiveTaskConflict``, not raw IntegrityError."""

    async def main():
        store = await Store.open(tmp_path / "state.db")
        try:
            await store.persist_created_task(
                task_id="t1",
                description="first",
                status="running",
                created_at=_TS,
                updated_at=_TS,
                worktrees=[("/repo/a", "agent/t1", "/repo/a.worktrees/t1")],
            )
            # Slug still active (running) — a second persist must be rejected typed.
            with pytest.raises(ActiveTaskConflict):
                await store.persist_created_task(
                    task_id="t1",
                    description="second",
                    status="running",
                    created_at=_TS,
                    updated_at=_TS,
                    worktrees=[("/repo/a", "agent/t1", "/repo/a.worktrees/t1")],
                )
            # Connection still usable after the rollback.
            return await store.get_task("t1")
        finally:
            await store.close()

    row = asyncio.run(main())
    assert row["description"] == "first"  # original untouched


def test_refuses_newer_schema_version(tmp_path):
    db = tmp_path / "state.db"

    async def setup():
        store = await Store.open(db)
        await store.close()

    async def bump():
        conn = await aiosqlite.connect(db)
        await conn.execute("PRAGMA user_version=999")
        await conn.commit()
        await conn.close()

    async def reopen():
        with pytest.raises(DevHelperError):
            await Store.open(db)

    asyncio.run(setup())
    asyncio.run(bump())
    asyncio.run(reopen())
