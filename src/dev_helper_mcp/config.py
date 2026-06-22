"""Single source of truth for tunable constants.

No magic numbers should live anywhere else in the codebase; import from here.
"""

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
