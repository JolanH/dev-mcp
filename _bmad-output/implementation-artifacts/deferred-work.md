# Deferred Work

## Deferred from: code review of story-1-1 (2026-06-22)

- **TOCTOU port race** [src/dev_helper_mcp/server.py:18-44] — `find_free_port` binds+closes a probe socket, then `uvicorn.run` re-binds the same port by number. Between the probe close and the uvicorn bind another process can grab the port, crashing with an unhandled `OSError` (no retry/fallback). `SO_REUSEADDR` on the probe widens the gap. Robust fix: hand the already-bound socket to uvicorn (`uvicorn.Server` with a passed socket) instead of re-binding by number. Deferred to Story 3.1 (single-instance protection / lockfile); low real-world impact for a localhost single-user tool.
- **`Mount("/", app=mcp_app)` shadows future routes** [src/dev_helper_mcp/server_factory.py:75] — the MCP sub-app is mounted at `/` and owns the entire URL space, so any dashboard/sibling route later added to the parent Starlette app will be unreachable. This wiring is the mandated 307-fix for Story 1.1, but Epic 2's dashboard (`/state` + board at `/`) must revisit it — e.g. register dashboard routes inside the MCP app, mount MCP at a sub-path, or order routes so the dashboard wins. Deferred to Epic 2.
