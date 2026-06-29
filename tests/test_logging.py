"""Logging audit (Story 3.3, AC4 / NFR-7 Observability).

Confirms the two observable properties of the logging contract:

* ``_configure_logging()`` reads ``DEV_HELPER_LOG`` (default ``INFO``) and writes
  to **stderr** (stdlib ``logging.basicConfig`` default stream).
* A failed tool call is *diagnosable from the logs* — it emits its stable
  ``error.code`` — while **never** emitting a user description/annotation body at
  ``INFO`` (descriptions are user prose: log ``task_id``/``status``/``code``, not
  the body).

Driven without real git: the ``update_task`` → ``TaskNotFound`` path needs only a
tmp ``Store`` (no worktrees, no ``tmp_git_repo``).
"""

import asyncio
import logging
import sys

from dev_helper_mcp.cache import Cache
from dev_helper_mcp.cli import _configure_logging
from dev_helper_mcp.git.repo_lock import RepoLockRegistry
from dev_helper_mcp.git.runner import GitRunner
from dev_helper_mcp.store import Store
from dev_helper_mcp.tools.handlers import ToolDeps, update_task
from dev_helper_mcp.tools.models import UpdateTaskIn


# ── _configure_logging(): level from env (default INFO), stream is stderr ──


def _reconfigure_from_clean_root(monkeypatch, env_value):
    """Run ``_configure_logging()`` from a handler-less root so ``basicConfig``
    actually applies (it is a no-op when the root already has handlers, which it
    does under pytest), then restore the original root state.

    Returns ``(effective_level, handlers)`` captured right after configuration.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    for h in saved_handlers:
        root.removeHandler(h)
    root.setLevel(logging.WARNING)  # a non-INFO sentinel so a real change is visible

    if env_value is None:
        monkeypatch.delenv("DEV_HELPER_LOG", raising=False)
    else:
        monkeypatch.setenv("DEV_HELPER_LOG", env_value)

    try:
        _configure_logging()
        return root.level, root.handlers[:]
    finally:
        for h in root.handlers[:]:
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def test_default_level_is_info(monkeypatch):
    level, _ = _reconfigure_from_clean_root(monkeypatch, None)
    assert level == logging.INFO


def test_env_raises_effective_level_to_debug(monkeypatch):
    level, _ = _reconfigure_from_clean_root(monkeypatch, "DEBUG")
    assert level == logging.DEBUG


def test_unknown_level_falls_back_to_info(monkeypatch):
    level, _ = _reconfigure_from_clean_root(monkeypatch, "NOPE")
    assert level == logging.INFO


def test_logs_to_stderr_not_stdout(monkeypatch):
    _, handlers = _reconfigure_from_clean_root(monkeypatch, None)
    streams = [getattr(h, "stream", None) for h in handlers]
    assert sys.stderr in streams
    assert sys.stdout not in streams


# ── failed tool call: error.code diagnosable, description body NOT at INFO ──

_SENSITIVE_BODY = "SECRET_USER_PROSE_THAT_MUST_NOT_LEAK_AT_INFO"


def test_failed_tool_logs_code_but_not_description_body(tmp_path, caplog):
    async def run():
        store = await Store.open(tmp_path / "state.db")
        try:
            runner = GitRunner()
            deps = ToolDeps(
                runner=runner,
                locks=RepoLockRegistry(),
                store=store,
                cache=Cache(runner=runner, store=store),
            )
            # Unknown task_id → TaskNotFound, carrying a user description body.
            return await update_task(
                UpdateTaskIn(task_id="ghost", status="review", description=_SENSITIVE_BODY),
                deps=deps,
            )
        finally:
            await store.close()

    with caplog.at_level(logging.INFO, logger="dev_helper_mcp.tools.handlers"):
        env = asyncio.run(run())

    # The call failed as a typed, error-as-data outcome.
    assert env["ok"] is False
    assert env["error"]["code"] == "TaskNotFound"

    info_records = [r for r in caplog.records if r.levelno >= logging.INFO]
    # Diagnosable: the failure left a server-side trace carrying the stable code.
    assert any("TaskNotFound" in r.getMessage() for r in info_records), (
        "a failed tool call must log its error.code so it is diagnosable from stderr"
    )
    # Not leaky: the user description body never appears at INFO or above.
    assert all(_SENSITIVE_BODY not in r.getMessage() for r in info_records), (
        "the user description body must never be logged at INFO (NFR-7)"
    )
