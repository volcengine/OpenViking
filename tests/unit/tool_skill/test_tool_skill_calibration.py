# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for tool/skill name calibration in memory extraction."""

from openviking.message.part import ToolPart
from openviking.session.compressor import SessionCompressor
from openviking.session.memory_extractor import MemoryCategory, ToolSkillCandidateMemory


def _candidate(
    category: MemoryCategory, tool_name: str = "", skill_name: str = ""
) -> ToolSkillCandidateMemory:
    return ToolSkillCandidateMemory(
        category=category,
        abstract="a",
        overview="o",
        content="c",
        source_session="s",
        user="u",
        tool_name=tool_name,
        skill_name=skill_name,
    )


class TestToolSkillCalibration:
    def test_tools_candidate_returns_only_canonical_tool_name(self):
        compressor = SessionCompressor.__new__(SessionCompressor)
        tool_parts = [
            ToolPart(tool_name="read_file", tool_status="completed"),
            ToolPart(skill_uri="viking://agent/skills/weather", tool_status="completed"),
            ToolPart(tool_name="weather", tool_status="error"),
        ]
        candidate = _candidate(MemoryCategory.TOOLS, tool_name="weather")
        tool_name, skill_name, status = compressor._get_tool_skill_info(candidate, tool_parts)
        assert tool_name == "weather"
        assert skill_name == ""
        assert status == "error"

    def test_skills_candidate_returns_only_canonical_skill_name(self):
        compressor = SessionCompressor.__new__(SessionCompressor)
        tool_parts = [
            ToolPart(tool_name="read_file", tool_status="completed"),
            ToolPart(skill_uri="viking://agent/skills/weather", tool_status="error"),
            ToolPart(tool_name="weather", tool_status="completed"),
        ]
        candidate = _candidate(MemoryCategory.SKILLS, skill_name="weather")
        tool_name, skill_name, status = compressor._get_tool_skill_info(candidate, tool_parts)
        assert tool_name == ""
        assert skill_name == "weather"
        assert status == "error"

    def test_empty_candidate_name_is_skipped(self):
        compressor = SessionCompressor.__new__(SessionCompressor)
        tool_parts = [ToolPart(tool_name="weather", tool_status="completed")]
        candidate = _candidate(MemoryCategory.TOOLS, tool_name="")
        tool_name, skill_name, status = compressor._get_tool_skill_info(candidate, tool_parts)
        assert (tool_name, skill_name, status) == ("", "", "completed")

    def test_no_match_returns_empty_and_never_falls_back_to_candidate(self):
        compressor = SessionCompressor.__new__(SessionCompressor)
        tool_parts = [ToolPart(tool_name="weather", tool_status="completed")]
        candidate = _candidate(MemoryCategory.TOOLS, tool_name="calendar")
        tool_name, skill_name, status = compressor._get_tool_skill_info(candidate, tool_parts)
        assert (tool_name, skill_name, status) == ("", "", "completed")

    def test_suffix_like_weather_usage_does_not_match_weather(self):
        compressor = SessionCompressor.__new__(SessionCompressor)
        tool_parts = [ToolPart(skill_uri="viking://agent/skills/weather", tool_status="completed")]
        candidate = _candidate(MemoryCategory.SKILLS, skill_name="weather使用")
        tool_name, skill_name, status = compressor._get_tool_skill_info(candidate, tool_parts)
        assert (tool_name, skill_name, status) == ("", "", "completed")

    def test_best_match_tie_picks_most_recent_tool_part(self):
        compressor = SessionCompressor.__new__(SessionCompressor)
        tool_parts = [
            ToolPart(tool_name="abcdeXghij", tool_status="completed"),
            ToolPart(tool_name="abcdeYghij", tool_status="error"),
        ]
        candidate = _candidate(MemoryCategory.TOOLS, tool_name="abcdefghij")
        tool_name, skill_name, status = compressor._get_tool_skill_info(candidate, tool_parts)
        assert tool_name == "abcdeYghij"
        assert skill_name == ""
        assert status == "error"
