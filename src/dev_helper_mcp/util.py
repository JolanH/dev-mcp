"""Pure helpers shared across layers. No mcp/starlette imports (seam anchor)."""

from datetime import datetime, timezone


def now_iso() -> str:
    """Current UTC time as ISO-8601 with a ``Z`` suffix, second precision.

    The single timestamp helper for the whole project. Never use the local-time
    ``datetime.now()`` or epoch integers.

    Example: ``2026-06-22T11:00:00Z``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
