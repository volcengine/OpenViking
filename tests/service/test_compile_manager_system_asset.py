# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.core.namespace_policy import SYSTEM_VIKINGBOT_SKILLS_ROOT
from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.session.user_id import UserIdentifier

COMPILE_MANAGER_URI = f"{SYSTEM_VIKINGBOT_SKILLS_ROOT}/compile-manager/SKILL.md"
COMPILE_MANAGER_ASSET = (
    Path(__file__).parents[2] / "openviking/system_assets/vikingbot/skills/compile-manager/SKILL.md"
)


@pytest.mark.asyncio
async def test_compile_manager_system_asset_is_mcp_readable_and_read_only(
    monkeypatch,
):
    expected = COMPILE_MANAGER_ASSET.read_text()
    viking_fs = SimpleNamespace(read_file=AsyncMock(return_value=expected))
    service = SimpleNamespace(fs=FSService(viking_fs=viking_fs))
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    token = mcp_endpoint._mcp_ctx.set(ctx)
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    try:
        content = await mcp_endpoint.read(COMPILE_MANAGER_URI)
    finally:
        mcp_endpoint._mcp_ctx.reset(token)

    assert isinstance(content, str)
    assert content.startswith("---\nname: compile-manager\n")
    assert "# Compile 管理" in content
    viking_fs.read_file.assert_awaited_once_with(COMPILE_MANAGER_URI, ctx=ctx)
