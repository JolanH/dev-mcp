"""Slug validation + slugify rules (AC 3)."""

import pytest

from dev_helper_mcp.config import SLUG_MAX_LENGTH
from dev_helper_mcp.core.slug import slugify
from dev_helper_mcp.errors import InvalidTaskName


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("My Feature", "my-feature"),
        ("  Trim  Me  ", "trim-me"),
        ("a--b__c", "a-b-c"),
        ("Hello, World!", "hello-world"),
        ("--lead-trail--", "lead-trail"),
        ("CamelCase123", "camelcase123"),
        ("fix/the.bug", "fix-the-bug"),
    ],
)
def test_slugify_valid(raw, expected):
    assert slugify(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", ".", "..", "!!!", "----", "-", "/", "***"])
def test_slugify_rejects_empty_or_reserved(raw):
    with pytest.raises(InvalidTaskName):
        slugify(raw)


def test_slugify_rejects_over_max_length():
    with pytest.raises(InvalidTaskName):
        slugify("a" * (SLUG_MAX_LENGTH + 1))


def test_slugify_allows_exactly_max_length():
    assert slugify("a" * SLUG_MAX_LENGTH) == "a" * SLUG_MAX_LENGTH


def test_slugify_rejects_non_string():
    with pytest.raises(InvalidTaskName):
        slugify(None)  # type: ignore[arg-type]
