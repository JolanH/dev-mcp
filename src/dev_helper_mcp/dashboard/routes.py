"""Read-only ``/state`` JSON endpoint (Story 2.3).

Serves the in-memory :class:`~dev_helper_mcp.projection.CacheSnapshot` (built by
Story 2.2's :class:`~dev_helper_mcp.cache.Cache`) as snake_case JSON. The single
non-obvious property: this is a PURE read of ``holder.deps.cache.current`` — one
in-memory ref read, **no git, no DB, no ``await`` on I/O** (Invariant 4; the whole
point of the derive-on-read cache — ``/state`` never shells out on a poll).

Adapter module — MAY import ``starlette`` (NOT in ``test_adapter_seam.py``'s
SEAM_MODULES). ``dataclasses.asdict`` over Story 2.1's snake_case frozen dataclass
fields IS the JSON contract (Invariant 3) — no translation layer, no hand-rolled
serializer that could drift from the snapshot shape.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .render import render_board

if TYPE_CHECKING:
    # Imported only for typing to avoid a circular import (server_factory imports
    # this module). The holder is the same mutable _DepsHolder the tool closures
    # capture — the route reads the loop-bound Cache through it at call time.
    from ..server_factory import _DepsHolder


def state_route(holder: _DepsHolder) -> Route:
    """Build the ``GET /state`` route, closing over the shared ``_DepsHolder``.

    Mirrors the closure-over-holder pattern in ``server_factory.build_mcp`` so the
    route and the tool closures share ONE deps source (Task 4 passes the same
    ``holder`` instance). ``methods=["GET"]`` only — a non-GET verb yields only a
    PARTIAL match on this route, so the catch-all ``Mount("/")`` listed behind it
    full-matches and returns the MCP app's 404 (NOT a 405). Either way no mutating
    action exists at ``/state`` — the read-only guarantee (AC3), proven exhaustively
    by ``test_route_table_is_read_only``.
    """

    async def state(request: Request) -> JSONResponse:
        # Guard the lifespan startup/teardown window: holder.deps is None before
        # Store.open()/after teardown nulls it (Decision A → 503 with a JSON body,
        # never a leaked stack trace). Mirrors the tools' "server not ready" guard.
        deps = holder.deps
        if deps is None or deps.cache is None:
            return JSONResponse({"detail": "server not ready"}, status_code=503)
        # Single in-memory ref read — by reference, no lock, no git, no DB, no await.
        # The snapshot is frozen and swapped whole, so a concurrent refresh() swap
        # cannot tear this read. asdict recurses the frozen CacheSnapshot into nested
        # snake_case dicts (tuples → JSON arrays) — the /state contract, no rename.
        snapshot = deps.cache.current
        return JSONResponse(dataclasses.asdict(snapshot))

    return Route("/state", state, methods=["GET"])


def board_route(holder: _DepsHolder) -> Route:
    """Build the ``GET /`` board route — the server-rendered HTML dashboard (Story 2.4a).

    Same closure-over-holder pattern as ``state_route``: reads the loop-bound Cache at
    call time. ``methods=["GET"]`` only — the page is READ-ONLY (FR-10): no form, no
    button, no mutating control. Listed BEFORE the catch-all ``Mount("/")`` in
    ``create_app`` (the route-ordering trick Story 2.3 established) so ``/`` resolves to
    the board while ``/mcp`` still falls through to the MCP app with no 307.

    The handler is a PURE read of ``holder.deps.cache.current`` rendered via the pure
    ``render_board`` — no git, no DB, no ``await`` on I/O (Invariant 4; the derive-on-read
    cache is never re-shelled on a page load). The live ``/state`` poll + diff-and-patch
    is Story 2.4b; freshness/degraded/empty states are Story 2.4c.
    """

    async def board(request: Request) -> HTMLResponse:
        # Guard the lifespan startup/teardown window (Decision A, mirrors /state): before
        # Store.open()/after teardown nulls deps → a minimal "server not ready" page with
        # a 503, never a leaked stack trace.
        deps = holder.deps
        if deps is None or deps.cache is None:
            return HTMLResponse(
                "<!doctype html><title>dev-helper-mcp</title><p>server not ready</p>",
                status_code=503,
            )
        # Single in-memory ref read (frozen, swapped whole — cannot tear), serialized to
        # the snake_case dict shape render_board consumes. asdict matches the /state
        # contract exactly, so the board and /state never drift.
        snapshot = dataclasses.asdict(deps.cache.current)
        return HTMLResponse(render_board(snapshot))

    return Route("/", board, methods=["GET"])
