# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A find() carrying a filter but no query resolves from the filter alone.

Looking a record up by an exact tag (an external id carried on the record, say)
has no meaningful query text. Forcing callers to invent one makes the similarity
score noise, and leaves them at the mercy of whatever recall the made-up query
happens to produce.
"""

import pytest

from openviking.service.search_service import _ensure_non_empty_query
from openviking.storage.viking_fs._base import (
    _ensure_filter_present,
    build_matched_context_from_record,
    is_filter_only_query,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType

TAG_FILTER = {"op": "must", "field": "search_tags", "conds": ["legacy_memory_id=mem-1"]}


def test_empty_query_with_filter_is_accepted():
    _ensure_non_empty_query("", None, TAG_FILTER)
    _ensure_non_empty_query("   ", None, TAG_FILTER)


def test_empty_query_without_filter_is_still_rejected():
    with pytest.raises(InvalidArgumentError):
        _ensure_non_empty_query("", None, None)
    with pytest.raises(InvalidArgumentError):
        _ensure_non_empty_query("   ", None, {})


def test_non_empty_query_is_always_accepted():
    _ensure_non_empty_query("hello", None, None)


def test_image_only_query_is_still_accepted():
    _ensure_non_empty_query("", "https://example.com/a.png", None)


@pytest.mark.parametrize(
    ("query", "image_url", "expected"),
    [
        ("", None, True),
        ("   ", None, True),
        ("\n\t", None, True),
        ("hello", None, False),
        ("", "https://example.com/a.png", False),
    ],
)
def test_is_filter_only_query(query, image_url, expected):
    assert is_filter_only_query(query, image_url) is expected


def test_ensure_filter_present_rejects_missing_filter():
    with pytest.raises(InvalidArgumentError):
        _ensure_filter_present(None)
    with pytest.raises(InvalidArgumentError):
        _ensure_filter_present({})


def test_ensure_filter_present_accepts_filter():
    _ensure_filter_present(TAG_FILTER)


def test_matched_context_from_record_keeps_tags_and_zero_score():
    matched = build_matched_context_from_record(
        {
            "uri": "viking://user/default/memories/s/a.md",
            "context_type": "memory",
            "level": 2,
            "abstract": "an abstract",
            "category": "note",
            "search_tags": ["legacy_memory_id=mem-1"],
        }
    )
    assert matched.uri == "viking://user/default/memories/s/a.md"
    assert matched.context_type == ContextType.MEMORY
    assert matched.search_tags == ["legacy_memory_id=mem-1"]
    # No similarity ranking took place, so the score must not be fabricated.
    assert matched.score == 0.0
    assert matched.match_reason == "filter"


def test_matched_context_from_record_without_uri_is_dropped():
    assert build_matched_context_from_record({"context_type": "memory"}) is None


def test_matched_context_from_record_falls_back_on_unknown_type():
    matched = build_matched_context_from_record(
        {"uri": "viking://user/default/resources/a.md", "context_type": "not-a-type"}
    )
    assert matched.context_type == ContextType.RESOURCE


def test_matched_context_keeps_level_zero():
    """Level 0 (a directory abstract) must survive, not collapse to the default."""
    matched = build_matched_context_from_record(
        {"uri": "viking://user/default/memories/s", "context_type": "memory", "level": 0}
    )
    assert matched.level == 0


def test_matched_context_defaults_level_when_absent():
    matched = build_matched_context_from_record(
        {"uri": "viking://user/default/memories/s/a.md", "context_type": "memory"}
    )
    assert matched.level == 2
