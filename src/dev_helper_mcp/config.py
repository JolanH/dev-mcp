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
#: Current schema version stamped into ``PRAGMA user_version``. Opening a DB
#: with a *newer* version is refused (version-check-only migrations).
SCHEMA_VERSION = 1
#: SQLite ``busy_timeout`` (ms): a polling dashboard reads while tools write;
#: WAL + busy_timeout avoids spurious ``SQLITE_BUSY``.
SQLITE_BUSY_TIMEOUT_MS = 5000


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
