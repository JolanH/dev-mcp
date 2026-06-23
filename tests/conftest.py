"""Shared test fixtures.

The baseline harness is an in-process ``httpx.ASGITransport`` client against the
parent Starlette app — no real port, no sockets. Async tests are driven with
``asyncio.run`` so no pytest-asyncio plugin is required (dev deps are kept to
ruff + pytest + httpx per the story).

The single real-port uvicorn smoke test lives in ``test_smoke_uvicorn.py`` and
is ``slow``-marked.
"""

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from dev_helper_mcp.config import DEFAULT_PORT
from dev_helper_mcp.server_factory import create_app

# ── Git-safety guard (project-context.md "Git safety in tests") ──
# The suite treats THIS project's own repository as strictly read-only. A test
# that runs git against a path resolving to this working tree once altered branch
# `master` and lost code; that must never recur. tests/ lives directly under the
# project root, so parent.parent is the repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# GIT_*-stripped env so the read-only signature below targets the real working
# tree even when pytest runs inside the pre-commit hook (which exports GIT_DIR /
# GIT_WORK_TREE / GIT_INDEX_FILE into the process).
_CLEAN_GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
_CLEAN_GIT_ENV["GIT_TERMINAL_PROMPT"] = "0"


def _project_repo_signature() -> str | None:
    """Read-only fingerprint of the project repo's refs + HEAD + current branch.

    Returns ``None`` if the project root is not a git repo (e.g. running from an
    unpacked sdist), making the guard a no-op there. Every git invocation is
    read-only (``rev-parse`` / ``symbolic-ref`` / ``for-each-ref``).
    """

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), *args],
            capture_output=True,
            text=True,
            env=_CLEAN_GIT_ENV,
        )

    inside = git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    head = git("rev-parse", "HEAD").stdout.strip()
    branch = git("symbolic-ref", "--quiet", "HEAD").stdout.strip()  # "" if detached
    refs = git("for-each-ref", "--format=%(refname) %(objectname)").stdout
    return f"HEAD={head}\nBRANCH={branch}\nREFS=\n{refs}"


@pytest.fixture(scope="session")
def _project_repo_baseline() -> str | None:
    return _project_repo_signature()


@pytest.fixture(autouse=True)
def _guard_project_repo_untouched(_project_repo_baseline):
    """HARD GUARD: no test may mutate the project's own git repository.

    Regression guard for the incident where a test run altered branch ``master``.
    Every test's git work must target a throwaway repo under ``tmp_path`` (the
    ``tmp_git_repo`` fixture), never this repo. Asserts the repo's refs/HEAD are
    byte-identical after each test, pinpointing the first offender.
    """
    yield
    if _project_repo_baseline is None:
        return
    after = _project_repo_signature()
    assert after == _project_repo_baseline, (
        "A test mutated the PROJECT's own git repository (refs/HEAD changed). "
        "Tests must run git only against a tmp_path repo (see the tmp_git_repo "
        "fixture); the main repository is read-only to the suite. "
        "See project-context.md 'Git safety in tests'."
    )


# Port baked into the app + the Origin allowlist for the in-process tests.
TEST_PORT = DEFAULT_PORT

# In-process base URL. Uses 127.0.0.1:<port> (not the httpx default "testserver")
# so the synthesised Host header passes FastMCP's own host validation.
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """Redirect the machine-global state dir to a per-test tmp dir.

    The app lifespan now opens the default ``Store`` (``create_task`` deps), so
    without this every lifespan-driving test would touch the real
    ``~/.local/state/dev-helper-mcp/state.db``. ``state_dir()`` reads the env on
    each call, so this isolates the DB per test.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture
def base_url() -> str:
    return BASE_URL


@pytest.fixture
def app():
    """The parent Starlette app bound to TEST_PORT."""
    return create_app(TEST_PORT)


@pytest.fixture
def asgi_client_factory(app):
    """Return an httpx.AsyncClient factory talking to ``app`` in-process.

    Shaped as an ``McpHttpClientFactory`` so it can also be handed to the MCP
    SDK's ``streamablehttp_client``.
    """

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            # raise_app_exceptions=False so an un-started session manager (when a
            # test deliberately skips the lifespan) surfaces as a 5xx response
            # rather than a raised exception.
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=BASE_URL,
            headers=headers,
            timeout=timeout if timeout is not None else httpx.Timeout(30.0),
            auth=auth,
        )

    return factory


@pytest.fixture
def tmp_git_repo(tmp_path):
    """A real, initialized git repo with one commit (reused by Stories 1.2–1.5).

    Uses ``subprocess`` directly — the "single run_git() only" rule governs
    ``src/`` runtime code, not test scaffolding.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    # Strip every inherited GIT_* var. When pytest runs inside a git hook
    # (e.g. the pre-commit gate), git exports GIT_DIR / GIT_INDEX_FILE /
    # GIT_WORK_TREE etc. into the hook process; left in place they redirect
    # these subprocess git calls at the *outer* repo instead of this tmp repo,
    # so the git-based tests pass in a bare shell but fail under the hook.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            env=env,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "README.md").write_text("hi\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return repo
