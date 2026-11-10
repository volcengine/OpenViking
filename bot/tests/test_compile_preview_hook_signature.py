# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression: the Compile preview hook must accept the keyword arguments the
AgentLoop preview hook is invoked with."""

import pytest
from vikingbot.compile.pipeline_agent import complete_tool_result


@pytest.mark.parametrize("preserve_complete", [False, True])
async def test_complete_tool_result_accepts_preserve_complete(preserve_complete):
    result = await complete_tool_result(
        "payload", "session-key", preserve_complete=preserve_complete
    )
    assert result == "payload"
