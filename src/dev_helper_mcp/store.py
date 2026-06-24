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
from .errors import ActiveTaskConflict, Internal

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

    async def persist_created_task(
        self,
        *,
        task_id: str,
        description: str,
        status: str,
        created_at: str,
        updated_at: str,
        worktrees: list[tuple[str, str, str]],
    ) -> None:
        """Atomically persist a created task: the ``task`` row + all its
        ``task_worktree`` rows in ONE transaction (no intermediate commit) — the
        rows are written *last*, after every worktree was provisioned (AC 2).

        Each ``worktrees`` entry is ``(repo_path, branch, worktree_path)``.

        Retask of a ``done`` slug (AC 4 success path): a same-``task_id`` row whose
        status is ``done`` is cleared first via a ``status='done'``-scoped DELETE,
        whose ``ON DELETE CASCADE`` purges its stale ``task_worktree`` rows; the
        fresh rows then insert cleanly. Scoping the DELETE to ``done`` is what makes
        the TOCTOU safety net work: if a *non-done* (active) row sneaks in past the
        preflight gate, the DELETE is a no-op, the INSERT collides on the PK, and the
        resulting ``IntegrityError`` is mapped to ``ActiveTaskConflict`` — never an
        allowlist, never a silent clobber of an active task.
        """
        try:
            # Clear only a reusable (done) prior record; cascade purges its links.
            await self._conn.execute(
                "DELETE FROM task WHERE task_id = ? AND status = 'done'", (task_id,)
            )
            await self._conn.execute(
                "INSERT INTO task (task_id, description, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, description, status, created_at, updated_at),
            )
            await self._conn.executemany(
                "INSERT INTO task_worktree (task_id, repo_path, branch, worktree_path) "
                "VALUES (?, ?, ?, ?)",
                [(task_id, repo_path, branch, wt_path) for repo_path, branch, wt_path in worktrees],
            )
            await self._conn.commit()
        except aiosqlite.IntegrityError as exc:
            await self._conn.rollback()
            msg = str(exc)
            # A live `task` PK clash ("UNIQUE constraint failed: task.task_id")
            # means an active task already owns this slug (TOCTOU race behind the
            # preflight gate) → ActiveTaskConflict. Every OTHER integrity failure
            # (a `task_worktree` PK clash, a CHECK/NOT NULL/FK violation) is a broken
            # core invariant, NOT a slug conflict — surface it as Internal rather
            # than blanket-mapping all integrity errors to ActiveTaskConflict.
            if "task.task_id" in msg:
                raise ActiveTaskConflict(
                    "task slug already in use by an active task",
                    {"task_id": task_id},
                ) from exc
            raise Internal(
                "task persistence integrity violation (invariant break)",
                {"task_id": task_id, "detail": msg},
            ) from exc
        except aiosqlite.Error as exc:
            # Operational failure (disk full, database locked, …): roll back so the
            # connection is left usable for subsequent calls, then surface typed.
            await self._conn.rollback()
            raise Internal(
                "task persistence failed",
                {"task_id": task_id, "detail": str(exc)},
            ) from exc

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

    async def list_worktree_links(
        self, *, repo: str | None = None, task_id: str | None = None
    ) -> list[dict]:
        """Return ``task_worktree`` rows joined to their ``task`` status (Story 1.5).

        Read-only; parameterized; ordered for stable output. Each row is
        ``{task_id, repo_path, branch, worktree_path, status}``. Optional ``repo`` /
        ``task_id`` narrow the result (callers pass a canonical abspath for ``repo``).
        This is the stored side of the AC1 live-git × store join — worktree
        *existence* is still derived from git, never read here.
        """
        sql = (
            "SELECT tw.task_id, tw.repo_path, tw.branch, tw.worktree_path, t.status "
            "FROM task_worktree tw JOIN task t ON t.task_id = tw.task_id"
        )
        conditions: list[str] = []
        params: list[str] = []
        if repo is not None:
            conditions.append("tw.repo_path = ?")
            params.append(repo)
        if task_id is not None:
            conditions.append("tw.task_id = ?")
            params.append(task_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY tw.task_id, tw.repo_path"

        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        keys = ("task_id", "repo_path", "branch", "worktree_path", "status")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    async def delete_worktree(self, task_id: str, repo_path: str) -> None:
        """Drop ONE ``task_worktree`` link by its ``(task_id, repo_path)`` PK (Story 1.5).

        Sibling links for the same ``task_id`` in other repos are untouched. The
        ``task`` row is NOT removed here — removing the task's *last* worktree is the
        caller's AC5 concern (``count_worktrees`` + ``delete_task``).
        """
        await self._conn.execute(
            "DELETE FROM task_worktree WHERE task_id = ? AND repo_path = ?",
            (task_id, repo_path),
        )
        await self._conn.commit()

    async def count_worktrees(self, task_id: str) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM task_worktree WHERE task_id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def delete_task(self, task_id: str) -> None:
        await self._conn.execute("DELETE FROM task WHERE task_id = ?", (task_id,))
        await self._conn.commit()

    async def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        description: str | None = None,
        updated_at: str,
    ) -> bool:
        """Update a task's ``status``/``description`` and bump ``updated_at`` (Story 1.6).

        The ``SET`` clause is built dynamically from the provided fields (only the
        non-``None`` ``status``/``description``) PLUS always ``updated_at``;
        ``created_at`` is NEVER touched (preserved — architecture.md:336-337). Values go
        through parameterized placeholders only (no string interpolation of values).
        Returns whether a row matched (``rowcount == 1``) so core can raise
        ``TaskNotFound`` on ``False``. A DB ``CHECK`` violation is NOT mapped here —
        core validates the status first, so the CHECK is a defensive backstop, never the
        rejection path.
        """
        sets: list[str] = []
        params: list[str] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        sets.append("updated_at = ?")
        params.append(updated_at)
        params.append(task_id)
        sql = f"UPDATE task SET {', '.join(sets)} WHERE task_id = ?"
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor.rowcount == 1

    async def list_tasks(self, *, status: str | None = None, repo: str | None = None) -> list[dict]:
        """Return one dict per matching task with all model fields + nested links (Story 1.6).

        Each entry is ``{task_id, description, status, created_at, updated_at,
        worktrees: [{repo_path, branch, worktree_path}, …]}``. Filters: ``status``
        narrows the ``task`` rows; ``repo`` returns only tasks that *touch* that repo,
        with that task's links limited to it (callers pass a canonical abspath for
        ``repo``, mirroring :meth:`list_worktree_links`). Tasks are sorted by ``task_id``
        ASC, worktrees by ``repo_path`` ASC (stable order). Parameterized; read-only; no
        schema change.
        """
        task_sql = "SELECT task_id, description, status, created_at, updated_at FROM task"
        params: list[str] = []
        if status is not None:
            task_sql += " WHERE status = ?"
            params.append(status)
        task_sql += " ORDER BY task_id"
        async with self._conn.execute(task_sql, params) as cur:
            task_rows = await cur.fetchall()
        task_keys = ("task_id", "description", "status", "created_at", "updated_at")
        tasks = [dict(zip(task_keys, row, strict=True)) for row in task_rows]

        # Reuse the existing JOIN shape for the links (repo-limited when given); it
        # already orders by (task_id, repo_path) so the grouped lists are repo-sorted.
        links = await self.list_worktree_links(repo=repo)
        by_task: dict[str, list[dict]] = {}
        for link in links:
            by_task.setdefault(link["task_id"], []).append(
                {
                    "repo_path": link["repo_path"],
                    "branch": link["branch"],
                    "worktree_path": link["worktree_path"],
                }
            )

        result: list[dict] = []
        for task in tasks:
            worktrees = by_task.get(task["task_id"], [])
            # A repo filter returns only tasks that touch that repo.
            if repo is not None and not worktrees:
                continue
            result.append({**task, "worktrees": worktrees})
        return result

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
