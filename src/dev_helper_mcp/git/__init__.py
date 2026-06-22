"""Git layer (SDK-free seam anchor).

No module under this package may import ``mcp`` or ``starlette`` — the adapter
seam (Invariant 7) is enforced by ``tests/test_adapter_seam.py``. The real git
runner and porcelain parsing arrive in Story 1.2.
"""
