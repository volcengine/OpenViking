# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SkillProcessor None data handling.

Verifies that SkillProcessor raises a clear ValueError when
skill data is None, instead of falling through to the generic
'Unsupported data type' error.
"""

import pytest

from openviking.utils.skill_processor import SkillProcessor


class TestParseSkillNoneData:
    """SkillProcessor._parse_skill should reject None with a clear message."""

    def test_parse_skill_none_raises_value_error(self):
        """None data should raise ValueError with explicit message."""
        processor = SkillProcessor(vikingdb=None)
        with pytest.raises(ValueError, match="Skill data cannot be None"):
            processor._parse_skill(None)

    def test_parse_skill_none_not_unsupported_type(self):
        """None should NOT produce the generic 'Unsupported data type' message."""
        processor = SkillProcessor(vikingdb=None)
        with pytest.raises(ValueError) as exc_info:
            processor._parse_skill(None)
        assert "Unsupported data type" not in str(exc_info.value)

    def test_parse_skill_valid_dict_passes(self):
        """A valid dict should not raise."""
        processor = SkillProcessor(vikingdb=None)
        skill_dict, aux_files, base_path = processor._parse_skill(
            {"name": "test-skill", "description": "A test skill"}
        )
        assert skill_dict["name"] == "test-skill"
        assert aux_files == []
        assert base_path is None

    def test_parse_skill_unsupported_type_still_raises(self):
        """Non-None unsupported types should still raise with type info."""
        processor = SkillProcessor(vikingdb=None)
        with pytest.raises(ValueError, match="Unsupported data type"):
            processor._parse_skill(12345)
