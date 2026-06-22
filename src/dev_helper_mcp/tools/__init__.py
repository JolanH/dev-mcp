"""Adapter layer: MCP tool I/O models + handlers.

This package MAY import ``mcp``/``pydantic`` — it is the boundary between the SDK
and the SDK-free core. It is deliberately NOT scanned by ``test_adapter_seam.py``
(which polices ``core/``, ``git/``, ``store.py``, ``projection.py``, ``cache.py``).
"""
