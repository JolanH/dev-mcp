"""Dashboard adapter package (FR-8–10).

Adapter layer — MAY import ``starlette``. Story 2.3 adds only the read-only
``/state`` JSON endpoint; Stories 2.4a/b/c add the HTML board + JS poller to this
same package. NOT scanned by ``tests/test_adapter_seam.py`` (that policed seam
covers ``core/``/``git/``/``store``/``projection``/``cache`` only).
"""
