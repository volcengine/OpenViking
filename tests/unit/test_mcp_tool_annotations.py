# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Contract tests for MCP tool behavior annotations."""

import openviking.server.mcp_endpoint as mcp_endpoint


async def test_mcp_tools_advertise_behavior_annotations():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    expected = {
        "find": (True, False, True, False),
        # Context mode can persist and prune the per-session recall ledger.
        "search": (False, True, False, False),
        "read": (True, False, True, False),
        "list": (True, False, True, False),
        "tree": (True, False, True, False),
        "remember": (False, True, False, False),
        "write": (False, True, False, False),
        "edit": (False, True, False, False),
        "add_resource": (False, True, False, True),
        "add_skill": (False, True, False, True),
        "list_watches": (True, False, True, False),
        "cancel_watch": (False, True, True, False),
        "grep": (True, False, True, False),
        "glob": (True, False, True, False),
        "forget": (False, True, True, False),
        "health": (True, False, True, False),
    }
    fields = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

    observed = {}
    for name, tool in tools.items():
        assert tool.annotations is not None
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        observed[name] = tuple(annotations[field] for field in fields)

    assert observed == expected
