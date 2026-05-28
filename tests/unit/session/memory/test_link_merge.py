# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.merge_op.link_merge import (
    _dedup_key,
    merge_links,
)


class TestDedupKey:
    def test_same_links_same_key(self):
        link1 = {"from_uri": "a", "to_uri": "b", "match_text": "foo"}
        link2 = {"from_uri": "a", "to_uri": "b", "match_text": "foo"}
        assert _dedup_key(link1) == _dedup_key(link2)

    def test_different_match_text_different_key(self):
        link1 = {"from_uri": "a", "to_uri": "b", "match_text": "foo"}
        link2 = {"from_uri": "a", "to_uri": "b", "match_text": "bar"}
        assert _dedup_key(link1) != _dedup_key(link2)


class TestMergeLinks:
    def test_empty_inputs(self):
        assert merge_links([], []) == []

    def test_new_links_added(self):
        existing = []
        new = [{"from_uri": "a", "to_uri": "b", "link_type": "related_to", "weight": 0.8}]
        result = merge_links(existing, new)
        assert len(result) == 1
        assert result[0]["weight"] == 0.8

    def test_weight_conflict_takes_max(self):
        existing = [
            {
                "from_uri": "a",
                "to_uri": "b",
                "match_text": "x",
                "weight": 0.5,
                "link_type": "related_to",
            }
        ]
        new = [
            {
                "from_uri": "a",
                "to_uri": "b",
                "match_text": "x",
                "weight": 0.9,
                "link_type": "belongs_to",
            }
        ]
        result = merge_links(existing, new)
        assert len(result) == 1
        assert result[0]["weight"] == 0.9
        # link_type: latest wins
        assert result[0]["link_type"] == "belongs_to"

    def test_description_latest_wins(self):
        existing = [
            {
                "from_uri": "a",
                "to_uri": "b",
                "match_text": "x",
                "description": "old",
            }
        ]
        new = [
            {
                "from_uri": "a",
                "to_uri": "b",
                "match_text": "x",
                "description": "new",
            }
        ]
        result = merge_links(existing, new)
        assert result[0]["description"] == "new"

    def test_different_match_text_not_deduped(self):
        existing = [{"from_uri": "a", "to_uri": "b", "match_text": "foo"}]
        new = [{"from_uri": "a", "to_uri": "b", "match_text": "bar"}]
        result = merge_links(existing, new)
        assert len(result) == 2
