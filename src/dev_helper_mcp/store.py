"""SQLite persistence — the ONLY module that opens the DB (core layer, no SDK import).

Two tables (``task`` + ``task_worktree``) with ``ON DELETE CASCADE``; WAL +
``busy_timeout`` + ``foreign_keys=ON`` per connection at bootstrap; version-check
-only migrations via ``PRAGMA user_version`` (opening a *newer* DB is refused).

Derive-on-read (Invariant 4): we store ONLY task records + their per-repo
``(repo_path, branch, worktree_path)`` links. Worktree *existence* is never
persisted — git porcelain is the sole truth for that (consumed in Epic 2).
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from .config import SCHEMA_VERSION, SQLITE_BUSY_TIMEOUT_MS, default_db_path
from .errors import Internal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
  task_id     TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('running','blocked','review','done')),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_worktree (
  task_id       TEXT NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
  repo_path     TEXT NOT NULL,
  branch        TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  PRIMARY KEY (task_id, repo_path)
);
"""


class Store:
    """Async SQLite store over a single owned connection.

    Open via :meth:`open` (connect + bootstrap). Methods use parameterized
    queries only; no ORM. Task-conflict / UPSERT / derive-on-read logic lives in
    later stories — this is the persistence substrate.
    """

    def __init__(self, conn: aiosqlite.Connection, db_path: str | Path) -> None:
        self._conn = conn
        self.db_path = db_path

    @classmethod
    async def open(cls, db_path: str | Path | None = None) -> Store:
        """Connect to ``db_path`` (default machine-global) and bootstrap schema."""
        path: str | Path = default_db_path() if db_path is None else db_path
        if isinstance(path, Path) or (isinstance(path, str) and path != ":memory:"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        conn = await aiosqlite.connect(path)
        store = cls(conn, path)
        try:
            await store._bootstrap()
        except BaseException:
            await conn.close()
            raise
        return store

    async def _bootstrap(self) -> None:
        # PRAGMAs are per-connection — set them on this connection, every open.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        async with self._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        version = int(row[0]) if row else 0

        if version > SCHEMA_VERSION:
            raise Internal(
                "state.db schema is newer than this build supports; refusing to open",
                {"found_version": version, "supported_version": SCHEMA_VERSION},
            )

        await self._conn.executescript(_SCHEMA)
        if version < SCHEMA_VERSION:
            await self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await self._conn.commit()

    # ── primitives (parameterized; exercised broadly by create_task in 1.3) ──

    async def add_task(
        self,
        task_id: str,
        description: str,
        status: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO task (task_id, description, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, description, status, created_at, updated_at),
        )
        await self._conn.commit()

    async def add_worktree(
        self, task_id: str, repo_path: str, branch: str, worktree_path: str
    ) -> None:
        await self._conn.execute(
            "INSERT INTO task_worktree (task_id, repo_path, branch, worktree_path) "
            "VALUES (?, ?, ?, ?)",
            (task_id, repo_path, branch, worktree_path),
        )
        await self._conn.commit()

    async def get_task(self, task_id: str) -> dict | None:
        async with self._conn.execute(
            "SELECT task_id, description, status, created_at, updated_at "
            "FROM task WHERE task_id = ?",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        keys = ("task_id", "description", "status", "created_at", "updated_at")
        return dict(zip(keys, row, strict=True))

    async def count_worktrees(self, task_id: str) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM task_worktree WHERE task_id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def delete_task(self, task_id: str) -> None:
        await self._conn.execute("DELETE FROM task WHERE task_id = ?", (task_id,))
        await self._conn.commit()

    # ── introspection helpers ──

    async def table_names(self) -> list[str]:
        async with self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def journal_mode(self) -> str:
        async with self._conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        return str(row[0]).lower() if row else ""

    async def close(self) -> None:
        await self._conn.close()
