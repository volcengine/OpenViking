# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.factory import register_default_tools
from vikingbot.agent.tools.ov_file import VikingRuleSearchTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import Config, SessionKey


def test_rule_search_tool_describes_only_defined_applicability_fields():
    description = VikingRuleSearchTool().description

    assert "applies_when" in description
    assert "exception" in description
    assert "conditions" not in description


@pytest.mark.asyncio
async def test_rule_search_tool_submits_context_and_options(monkeypatch):
    client = AsyncMock()
    client.rule_search.return_value = {
        "rendered": "# Applicable OpenViking Rules\n\n## [confirm-delete@1]",
        "rules": [
            {
                "atomic_id": "confirm-delete",
                "content": "Confirm before deleting files.",
                "probability": 0.91,
            }
        ],
        "judge": "jev_noul",
    }
    tool = VikingRuleSearchTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=client))
    registry = ToolRegistry()
    registry.register(tool)

    outcome = await registry.execute_detailed(
        tool.name,
        {"task_context": "Delete a generated file."},
        SessionKey(type="test", channel_id="channel", chat_id="chat"),
    )

    client.rule_search.assert_awaited_once_with("Delete a generated file.")
    assert outcome.result == "# Applicable OpenViking Rules\n\n## [confirm-delete@1]"
    assert outcome.preserve_complete_result is True


@pytest.mark.asyncio
async def test_rule_search_tool_falls_back_for_older_server(monkeypatch):
    client = AsyncMock()
    client.rule_search.return_value = {
        "rules": [{"atomic_id": "legacy", "content": "Legacy response."}]
    }
    tool = VikingRuleSearchTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=client))

    result = await tool.execute(ToolContext(), task_context="legacy server")

    assert json.loads(result)["rules"][0]["atomic_id"] == "legacy"


@pytest.mark.asyncio
async def test_rule_search_result_bypasses_generic_tool_preview_truncation():
    loop = object.__new__(AgentLoop)
    tool = VikingRuleSearchTool()
    complete_rules = "complete-rule\n" * 1_000

    result = await loop._preview_tool_result(
        complete_rules,
        SessionKey(type="test", channel_id="channel", chat_id="chat"),
        preserve_complete=tool.preserve_complete_result,
    )

    assert result == complete_rules
    assert "[Output truncated." not in result


@pytest.mark.parametrize("mode", ["normal", "readonly"])
def test_rule_search_tool_is_registered_in_all_openviking_modes(mode):
    config = Config(mode=mode)
    registry = ToolRegistry(config=config)

    register_default_tools(registry, config)

    assert registry.has("openviking_rule_search")
    assert registry.has("openviking_add_resource") is (mode != "readonly")
