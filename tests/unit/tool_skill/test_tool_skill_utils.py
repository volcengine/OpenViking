# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.message.part import ToolPart
from openviking.session.tool_skill_utils import (
    calibrate_skill_name,
    calibrate_tool_name,
    collect_skill_stats,
    collect_tool_stats,
    normalize_name,
)


class TestToolSkillUtils:
    def test_normalize_name_removes_common_separators(self):
        assert normalize_name(" get-weather ") == normalize_name("get_weather")
        assert normalize_name("Get Weather") == normalize_name("get_weather")

    def test_calibrate_tool_name_matches_hyphen_to_underscore(self):
        tool_parts = [ToolPart(tool_name="get_weather", tool_status="completed")]
        tool_name, status = calibrate_tool_name("get-weather", tool_parts)
        assert tool_name == "get_weather"
        assert status == "completed"

    def test_collect_tool_stats_aggregates_counts_and_tokens(self):
        tool_parts = [
            ToolPart(
                tool_name="get_weather",
                tool_status="completed",
                duration_ms=10,
                prompt_tokens=5,
                completion_tokens=7,
            ),
            ToolPart(
                tool_name="get_weather",
                tool_status="error",
                duration_ms=20,
                prompt_tokens=1,
                completion_tokens=2,
            ),
        ]
        stats = collect_tool_stats(tool_parts)["get_weather"]
        assert stats["call_count"] == 2
        assert stats["success_time"] == 1
        assert stats["duration_ms"] == 30
        assert stats["prompt_tokens"] == 6
        assert stats["completion_tokens"] == 9

    def test_calibrate_skill_name_matches_by_skill_uri_suffix(self):
        tool_parts = [ToolPart(skill_uri="viking://agent/skills/weather", tool_status="error")]
        tool_name, status = calibrate_skill_name("weather", tool_parts)
        assert tool_name == "weather"
        assert status == "error"

    def test_collect_skill_stats_aggregates_by_skill_name(self):
        tool_parts = [
            ToolPart(
                skill_uri="viking://agent/skills/weather",
                tool_status="completed",
                duration_ms=3,
                prompt_tokens=2,
                completion_tokens=4,
            ),
            ToolPart(
                skill_uri="viking://agent/skills/weather",
                tool_status="error",
                duration_ms=7,
                prompt_tokens=1,
                completion_tokens=1,
            ),
        ]
        stats = collect_skill_stats(tool_parts)["weather"]
        assert stats["call_count"] == 2
        assert stats["success_time"] == 1
