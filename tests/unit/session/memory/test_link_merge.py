# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.session.memory.merge_op.link_merge import (
    _dedup_key,
    is_allowed_wiki_link,
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


class TestWikiLinkDirection:
    overview = "viking://user/alice/resources/docs/.overview.md"
    entity = "viking://user/alice/memories/entities/projects/openviking.md"
    other_entity = "viking://user/alice/memories/entities/people/alice.md"
    profile = "viking://user/alice/memories/profile.md"
    preference = "viking://user/alice/memories/preferences/editor.md"

    @pytest.mark.parametrize(
        ("from_uri", "to_uri", "allowed"),
        [
            (overview, entity, True),
            (entity, overview, False),
            ("viking://user/alice/resources/docs/page.md", entity, False),
            ("viking://resources/docs/.overview.md", entity, False),
            (profile, entity, True),
            (entity, profile, False),
            (entity, other_entity, True),
            (
                entity,
                "viking://user/alice/peers/bob/memories/entities/people/bob.md",
                False,
            ),
            (profile, preference, True),
        ],
    )
    def test_direction_matrix(self, from_uri, to_uri, allowed):
        assert is_allowed_wiki_link(from_uri, to_uri) is allowed

    def test_legacy_memory_resource_reference_is_preserved(self):
        assert is_allowed_wiki_link(
            self.entity,
            "viking://resources/id_card.pdf",
            "references_resource",
        )

    def test_global_resource_targets_request_users_entities(self):
        overview = "viking://resources/docs/.overview.md"
        alice_ctx = SimpleNamespace(
            user=SimpleNamespace(user_id="alice"),
            actor_peer_id=None,
        )
        bob_ctx = SimpleNamespace(
            user=SimpleNamespace(user_id="bob"),
            actor_peer_id=None,
        )
        peer_ctx = SimpleNamespace(
            user=SimpleNamespace(user_id="alice"),
            actor_peer_id="bob",
        )
        peer_entity = "viking://user/alice/peers/bob/memories/entities/openviking.md"

        assert is_allowed_wiki_link(overview, self.entity, ctx=alice_ctx)
        assert not is_allowed_wiki_link(overview, self.entity, ctx=bob_ctx)
        assert is_allowed_wiki_link(overview, peer_entity, ctx=peer_ctx)
        assert not is_allowed_wiki_link(overview, self.entity, ctx=peer_ctx)


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
