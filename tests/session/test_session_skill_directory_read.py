# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression for session skill extraction directory-read guidance (#4831)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.session.skill.session_skill_context_provider import (
    SessionSkillContextProvider,
)


@pytest.mark.asyncio
async def test_execute_tool_rejects_directory_uri_without_list_guidance():
    """Directory reads must not surface the unfollowable 'List it first' error."""
    provider = SessionSkillContextProvider(messages=[])
    provider._viking_fs = SimpleNamespace(read_file=AsyncMock())

    result = await provider.execute_tool(
        SimpleNamespace(name="read", arguments={"uri": "viking://user/alice/skills"})
    )

    assert "error" in result
    assert "List it first" not in result["error"]
    assert "no `list` tool" in result["error"]
    assert ".../SKILL.md" in result["error"]
    provider._viking_fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_still_reads_skill_md():
    provider = SessionSkillContextProvider(messages=[])
    provider._viking_fs = SimpleNamespace(
        read_file=AsyncMock(
            return_value=(
                "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n\nbody\n"
            )
        )
    )

    result = await provider.execute_tool(
        SimpleNamespace(
            name="read",
            arguments={"uri": "viking://user/alice/skills/demo/SKILL.md"},
        )
    )

    assert result["name"] == "demo"
    assert result["description"] == "demo skill"
    provider._viking_fs.read_file.assert_awaited_once()


def test_instruction_forbids_directory_read():
    provider = SessionSkillContextProvider(messages=[])
    text = provider.instruction()
    assert "never on a directory path" in text
    assert ".../SKILL.md" in text
