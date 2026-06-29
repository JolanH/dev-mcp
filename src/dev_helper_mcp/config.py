"""Single source of truth for tunable constants.

No magic numbers should live anywhere else in the codebase; import from here.
"""

import os
from pathlib import Path

#: Human-facing application / MCP server name.
APP_NAME = "dev-helper-mcp"

#: URL path the Streamable HTTP MCP endpoint is served at (clients connect here,
#: with no 307 redirect — see server_factory wiring note).
MCP_PATH = "/mcp"

#: Preferred port; the server scans upward from here for the first free one.
DEFAULT_PORT = 8765

#: Inclusive port scan range 8765 -> 8775 (``range`` upper bound is exclusive).
PORT_RANGE = range(8765, 8776)

#: Loopback host the server binds to. NEVER 0.0.0.0 (NFR-Security).
BIND_HOST = "127.0.0.1"

#: Hosts permitted in the ``Origin`` header. The concrete allowlist of full
#: origins (scheme://host:port) is built per-run once the bound port is known
#: (see ``middleware.allowed_origins``) — the port cannot be hardcoded here.
ALLOWED_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost"})

# ── Async git execution — two latency-class pools (Invariant 1; arch §Async-git) ──

#: Read/refresh class: per-command timeout (seconds). Feeds the cache; never on
#: the poll path. Short so a hung repo fails fast and the cache keeps last state.
GIT_READ_TIMEOUT = 3.0
#: Read pool concurrency (max concurrent read/refresh git subprocesses).
GIT_READ_POOL_SIZE = 2
#: Fail-fast: if a read slot is not free within this long, raise GitTimeout
#: rather than queue (keep the cache rather than block the poll path).
GIT_READ_ACQUIRE_TIMEOUT = 2.0
#: Mutation class: generous bounded timeout — ``worktree add`` checkout can be
#: legitimately multi-second on a large/cold repo; stays under the ~5-min ceiling.
GIT_MUTATION_TIMEOUT = 120.0
#: Mutation pool concurrency.
GIT_MUTATION_POOL_SIZE = 4

#: Background cache-refresher tick period (seconds). The refresher rebuilds the
#: in-memory derive-on-read view every interval; 2.0s keeps worst-case background
#: staleness under the ≤3s freshness SLO (FR-9 / UX-DR6). This is the BACKGROUND
#: tick — distinct from the dashboard *poll* interval (a 2.4b UI concern).
CACHE_REFRESH_INTERVAL: float = 2.0

#: Dashboard live-poll interval (milliseconds) — the period the **browser** waits
#: between ``fetch("/state")`` calls in the inlined poller (Story 2.4b). This is a
#: distinct concern from ``CACHE_REFRESH_INTERVAL`` above: that is the SERVER tick
#: that rebuilds the in-memory view; this is the CLIENT poll that re-reads it. With
#: the 2.0s server tick they overlap to keep end-to-end staleness within the ≤3s
#: freshness SLO for ≤15 repos (Decision A, operator-confirmed 2026-06-25: 1500ms).
#: ``render_board`` injects this into the page so the poller reads it (no hardcoding
#: in the JS). The poller re-arms with ``setTimeout`` AFTER each poll resolves — never
#: ``setInterval`` — so a slow ``/state`` cannot stack overlapping in-flight requests.
DASHBOARD_POLL_INTERVAL_MS: int = 1500

#: Staleness factor (UX-DR6: "older than **2 × the poll interval**"). The effective
#: stale threshold the dashboard uses is ``DASHBOARD_POLL_INTERVAL_MS *
#: DASHBOARD_STALE_FACTOR`` (= 3000ms at the 1500ms poll). Staleness is computed
#: CLIENT-SIDE (it is time-relative and must keep advancing between polls even when
#: ``/state`` stops responding — Decision A); the server only injects this threshold
#: onto the page (``data-stale-threshold-ms``) so the inlined poller reads it without
#: hardcoding. ``render_board`` also honours it when a ``now_ms`` is INJECTED for a
#: deterministic stale-at-load render (Decision B) — it never reads a clock itself.
DASHBOARD_STALE_FACTOR: int = 2

#: Environment pinned on every git subprocess (merged over ``os.environ``):
#: no credential prompts, no optional lock churn. Never run git via a shell.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}

#: Repo-context git env vars that MUST be stripped from the inherited
#: environment before every git subprocess. We always select the repo
#: explicitly with ``-C <repo>``; if any of these leak in (most notably when
#: the server/tests run inside a git hook, where git exports them), they
#: silently override ``-C`` and point git at the wrong repo/index. Conservative
#: list — leaves legitimate user vars like ``GIT_SSH_COMMAND`` untouched.
GIT_CONTEXT_VARS = frozenset(
    {
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_PREFIX",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
)

# ── Slug rules (``<task>``) — core/slug.py ──

#: Max slug length; over-length names are rejected (never silently truncated).
SLUG_MAX_LENGTH = 60
#: Slugs that must be rejected even though they are "non-empty" pre-collapse.
RESERVED_SLUGS = frozenset({"", ".", ".."})

# ── Worktree / branch naming conventions (core/tasks.py) ──

#: Prefix for the per-task branch created in every repo: ``agent/<slug>``. The
#: ``agent/`` namespace is why RESERVED_SLUGS need not block ``main``/``master``.
BRANCH_PREFIX = "agent/"
#: Suffix of the per-repo worktree parent dir, a SIBLING of the repo:
#: ``<repo>.worktrees/<slug>`` (never inside the repo, never under ``src/``).
WORKTREE_DIR_SUFFIX = ".worktrees"


def worktree_path_for(repo: Path, slug: str) -> Path:
    """Absolute worktree path for ``slug`` in ``repo``: ``<repo>.worktrees/<slug>``.

    Pure path arithmetic (no filesystem I/O) — the worktree dir sits beside the
    repo, e.g. ``/code/myrepo`` → ``/code/myrepo.worktrees/<slug>``.
    """
    return repo.parent / f"{repo.name}{WORKTREE_DIR_SUFFIX}" / slug


def branch_name_for(slug: str) -> str:
    """The per-task branch name: ``agent/<slug>``."""
    return f"{BRANCH_PREFIX}{slug}"


def slug_from_worktree_cwd(cwd: str | Path) -> str | None:
    """Recover a task slug from a directory inside its worktree — the inverse of
    :func:`worktree_path_for`.

    ``worktree_path_for`` maps ``(repo, slug)`` -> ``<repo>.worktrees/<slug>``, so the
    slug is the path segment whose *parent* directory name ends with
    ``WORKTREE_DIR_SUFFIX``. Walk ``cwd`` and its ancestors (so it works from any
    sub-directory of the worktree, not just its root) and return the first such
    segment. Returns ``None`` when ``cwd`` is not inside a task worktree (e.g. the main
    repo, or anywhere outside one) — the caller treats that as "no task to update".

    Pure path arithmetic: no filesystem or git access, so it is deterministic and
    unit-testable without a real worktree. ``cwd`` is normalised lexically first
    (``os.path.normpath`` collapses ``.``/``..`` without I/O) so a path like
    ``<repo>.worktrees/<slug>/..`` does not mis-resolve to ``<slug>``.
    """
    path = Path(os.path.normpath(os.fspath(cwd)))
    for ancestor in (path, *path.parents):
        parent = ancestor.parent
        if parent.name.endswith(WORKTREE_DIR_SUFFIX) and len(parent.name) > len(
            WORKTREE_DIR_SUFFIX
        ):
            return ancestor.name
    return None


# ── Task status lifecycle (core/tasks.py update_task) ──

#: The four legal task statuses. Mirrors the SQL ``CHECK`` in ``store.py`` — keep the
#: two in sync (the CHECK is the DB backstop; this constant is the core-layer guard so
#: a bad status is rejected as typed-error-as-data BEFORE hitting the DB CHECK).
#: ``blocked`` = awaiting input, ``review`` = awaiting operator review (still active),
#: ``done`` = terminal.
TASK_STATUSES: tuple[str, ...] = ("running", "blocked", "review", "done")
#: The active (non-terminal) subset — a slug owned by an active task cannot be
#: re-created (the ``create_task`` active-gate is literally ``status != 'done'``).
ACTIVE_STATUSES = frozenset({"running", "blocked", "review"})
#: The single terminal status; a ``done`` task cannot be moved back to an active state
#: (re-activating a slug is a NEW ``create_task``, never ``update_task``).
TERMINAL_STATUS = "done"


def legal_transition(src: str, dst: str) -> bool:
    """Whether a status change from ``src`` to ``dst`` is permitted.

    From any active status (``running``/``blocked``/``review``) any of the four states
    is reachable — including active→``done`` and idempotent self-transitions. ``done``
    is terminal, so ``done → *`` is always illegal (including ``done → done``). This
    yields the 4×4 matrix: 12 legal (3 active source rows × 4) / 4 illegal (the
    ``done`` source row).
    """
    return dst in TASK_STATUSES and src != TERMINAL_STATUS


# ── Persistence — store.py ──

#: DB filename inside the machine-global state dir.
STATE_DB_NAME = "state.db"
#: Single-instance lockfile name, a sibling of the DB in the same state dir
#: (``server.lock``). The process-singleton guard (AR-10) — distinct from the
#: per-repo mutation mutex (AR-14). See ``lock.py``.
LOCKFILE_NAME = "server.lock"
#: Current schema version stamped into ``PRAGMA user_version``. Opening a DB
#: with a *newer* version is refused (version-check-only migrations).
SCHEMA_VERSION = 1
#: SQLite ``busy_timeout`` (ms): a polling dashboard reads while tools write;
#: WAL + busy_timeout avoids spurious ``SQLITE_BUSY``.
SQLITE_BUSY_TIMEOUT_MS = 5000


def start_task_skill_path() -> Path:
    """Canonical source of the ``start-task`` workflow: the same ``SKILL.md`` the
    Claude Code skill uses, read by the ``start_task`` MCP prompt so the workflow has
    ONE source of truth (no duplicated copy in Python).

    Anchored on the package location — ``<repo-root>/.claude/skills/start-task/SKILL.md``
    — valid for the editable/source checkout this dev tool runs from
    (``src/dev_helper_mcp/config.py`` → ``parents[2]`` is the repo root).
    """
    return Path(__file__).resolve().parents[2] / ".claude" / "skills" / "start-task" / "SKILL.md"


def state_dir() -> Path:
    """Machine-global runtime state dir: ``${XDG_STATE_HOME:-~/.local/state}/dev-helper-mcp``.

    Pure resolver (reads the env each call) so tests can override
    ``XDG_STATE_HOME``. Runtime state lives here — never under ``src/``.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return Path(base) / APP_NAME


def default_db_path() -> Path:
    """Absolute path to the machine-global SQLite DB."""
    return state_dir() / STATE_DB_NAME


def lockfile_path() -> Path:
    """Absolute path to the machine-global single-instance lockfile.

    Mirrors ``default_db_path()`` exactly — reads ``state_dir()`` on every call so
    the autouse ``XDG_STATE_HOME`` test isolation works (never cache at import).
    """
    return state_dir() / LOCKFILE_NAME
