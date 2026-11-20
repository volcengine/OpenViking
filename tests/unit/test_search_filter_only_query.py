# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A find() carrying a filter but no query resolves from the filter alone.

Looking a record up by an exact tag (an external id carried on the record, say)
has no meaningful query text. Forcing callers to invent one makes the similarity
score noise, and leaves them at the mercy of whatever recall the made-up query
happens to produce.
"""

from openviking.storage.viking_fs._base import build_matched_context_from_record
from openviking_cli.retrieve import ContextType


def test_matched_context_from_record_without_uri_is_dropped():
    assert build_matched_context_from_record({"context_type": "memory"}) is None


def test_matched_context_from_record_falls_back_on_unknown_type():
    matched = build_matched_context_from_record(
        {"uri": "viking://user/default/resources/a.md", "context_type": "not-a-type"}
    )
    assert matched.context_type == ContextType.RESOURCE


def test_filter_only_record_preserves_l0_and_defaults_legacy_records_to_l2():
    directory = {"uri": "viking://user/default/memories/s", "context_type": "memory"}
    assert build_matched_context_from_record({**directory, "level": 0}).level == 0

    legacy = {"uri": f"{directory['uri']}/a.md", "context_type": "memory"}
    assert build_matched_context_from_record(legacy).level == 2
