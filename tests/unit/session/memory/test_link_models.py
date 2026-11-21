# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.dataclass import WikiLink


class TestWikiLink:
    def test_omitted_link_fields_use_defaults(self):
        link = WikiLink(f=1, t=2, match_text=None)
        assert link.link_type == "related_to"
        assert link.weight == 0.5

    def test_weight_is_preserved_when_valid(self):
        link = WikiLink(f=1, t=2, weight=0.8, match_text=None)
        assert link.weight == 0.8

    def test_weight_is_clamped_to_zero_when_negative(self):
        link = WikiLink(f=1, t=2, weight=-0.2, match_text=None)
        assert link.weight == 0.0

    def test_weight_is_clamped_to_one_when_above_range(self):
        link = WikiLink(f=1, t=2, weight=1.7, match_text=None)
        assert link.weight == 1.0

    def test_weight_defaults_to_mid_value_when_invalid(self):
        link = WikiLink.model_validate(
            {"f": 1, "t": 2, "weight": "not-a-number", "match_text": None}
        )
        assert link.weight == 0.5

    def test_json_schema_requires_nullable_match_text(self):
        schema = WikiLink.model_json_schema()

        assert "match_text" in schema["required"]
        options = schema["properties"]["match_text"]["anyOf"]
        assert {option["type"] for option in options} == {"string", "null"}
