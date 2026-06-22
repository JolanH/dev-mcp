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

#: Environment pinned on every git subprocess (merged over ``os.environ``):
#: no credential prompts, no optional lock churn. Never run git via a shell.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}

# ── Slug rules (``<task>``) — core/slug.py ──

#: Max slug length; over-length names are rejected (never silently truncated).
SLUG_MAX_LENGTH = 60
#: Slugs that must be rejected even though they are "non-empty" pre-collapse.
RESERVED_SLUGS = frozenset({"", ".", ".."})

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
