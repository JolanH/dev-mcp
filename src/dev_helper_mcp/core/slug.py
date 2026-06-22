"""Task-name slug validation + slugify (pure core helper — no SDK import).

The returned slug is both the ``task_id`` and the ``agent/<task>`` branch name
reused across every repo a task spans, so it must be filesystem- and git-ref-safe.
Over-length names are rejected (not truncated) — a truncated slug could collide,
and create is all-or-nothing with no silent suffixing.
"""

from __future__ import annotations

import re

from ..config import RESERVED_SLUGS, SLUG_MAX_LENGTH
from ..errors import InvalidTaskName

#: Any run of characters outside ``[a-z0-9]`` becomes a single hyphen.
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Normalize ``name`` to a slug or raise :class:`InvalidTaskName`.

    Lowercases, replaces non-alphanumeric runs with a single ``-``, collapses
    duplicate / leading / trailing hyphens, and enforces the length cap.
    """
    if not isinstance(name, str):
        raise InvalidTaskName("task name must be a string", {"name": repr(name)})

    slug = _NON_SLUG.sub("-", name.strip().lower()).strip("-")

    if not slug or slug in RESERVED_SLUGS:
        raise InvalidTaskName("task name reduces to an empty or reserved slug", {"name": name})
    if len(slug) > SLUG_MAX_LENGTH:
        raise InvalidTaskName(
            f"task slug exceeds max length {SLUG_MAX_LENGTH}",
            {"name": name, "length": len(slug)},
        )
    return slug
