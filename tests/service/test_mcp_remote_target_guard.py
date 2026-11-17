# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.mcp_service import MCPService
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role(Role.USER))


async def test_add_rejects_a_loopback_mcp_endpoint():
    service = MCPService(SimpleNamespace())

    with pytest.raises(PermissionDeniedError):
        await service.add("local", "http://127.0.0.1:9/mcp", {}, _ctx())


async def test_validate_rejects_a_localhost_mcp_endpoint():
    service = MCPService(SimpleNamespace())

    with pytest.raises(PermissionDeniedError):
        await service.validate("local", "http://localhost:9/mcp", {})
