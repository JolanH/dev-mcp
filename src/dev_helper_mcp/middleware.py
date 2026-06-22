"""Origin-validation middleware (AR-4, NFR-Security).

This is OUR own middleware, not FastMCP's ``TransportSecurityMiddleware``:
because the MCP app is mounted as a sub-app, FastMCP's own security layer is
bypassed, so origin enforcement must live on the parent Starlette app as the
outermost layer guarding every route (``/mcp`` and the dashboard alike).

Rule:
- ``Origin`` present and NOT in the allowlist -> 403.
- ``Origin`` absent (non-browser MCP clients such as Claude Code) -> allow.
- ``Origin`` present and allowlisted -> allow.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp

from .config import ALLOWED_ORIGIN_HOSTS

logger = logging.getLogger(__name__)


def allowed_origins(port: int) -> frozenset[str]:
    """Build the exact set of permitted origins for the bound ``port``.

    The port is only known after scanning for a free one, so the allowlist
    must be constructed at app-build time rather than hardcoded.
    """
    return frozenset(f"http://{host}:{port}" for host in ALLOWED_ORIGIN_HOSTS)


class OriginValidationMiddleware(BaseHTTPMiddleware):
    """Reject requests whose ``Origin`` header is present but not allowlisted."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        super().__init__(app)
        self._allowed = allowed_origins(port)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        origin = request.headers.get("origin")
        if origin is not None and origin not in self._allowed:
            logger.warning("Rejected request with disallowed Origin: %s", origin)
            return PlainTextResponse("Forbidden: invalid Origin", status_code=403)
        return await call_next(request)
