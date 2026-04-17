# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.modules.setdefault("volcenginesdkarkruntime", SimpleNamespace())

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import ResourceExhaustedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("test_account", "test_user", "test_agent"),
        role=Role.USER,
    )


@pytest.fixture
def resource_service() -> ResourceService:
    return ResourceService(
        vikingdb=SimpleNamespace(),
        viking_fs=SimpleNamespace(),
        resource_processor=SimpleNamespace(
            process_resource=AsyncMock(return_value={"root_uri": "viking://resources/demo"})
        ),
        skill_processor=SimpleNamespace(
            process_skill=AsyncMock(
                return_value={"status": "success", "uri": "viking://agent/skills/demo"}
            )
        ),
    )


def test_add_resource_blocks_when_estimated_tokens_exceed_limit(
    resource_service: ResourceService,
    request_context: RequestContext,
    tmp_path,
):
    path = tmp_path / "large.md"
    path.write_text("token-guard " * 200, encoding="utf-8")
    resource_service.set_token_guardrails(add_resource=50)

    with pytest.raises(ResourceExhaustedError, match="add_resource estimated input tokens"):
        asyncio.run(
            resource_service.add_resource(
                path=str(path),
                ctx=request_context,
                reason="large import",
            )
        )

    resource_service._resource_processor.process_resource.assert_not_awaited()


def test_add_skill_blocks_when_estimated_tokens_exceed_limit(
    resource_service: ResourceService,
    request_context: RequestContext,
):
    resource_service.set_token_guardrails(add_skill=40)
    skill_markdown = "# Demo Skill\n\n" + ("very detailed guidance " * 80)

    with pytest.raises(ResourceExhaustedError, match="add_skill estimated input tokens"):
        asyncio.run(
            resource_service.add_skill(
                data=skill_markdown,
                ctx=request_context,
            )
        )

    resource_service._skill_processor.process_skill.assert_not_awaited()


def test_add_resource_allows_requests_within_limit(
    resource_service: ResourceService,
    request_context: RequestContext,
    tmp_path,
):
    path = tmp_path / "small.md"
    path.write_text("small file", encoding="utf-8")
    resource_service.set_token_guardrails(add_resource=100)

    result = asyncio.run(
        resource_service.add_resource(
            path=str(path),
            ctx=request_context,
            reason="ok",
        )
    )

    assert result["root_uri"] == "viking://resources/demo"
    resource_service._resource_processor.process_resource.assert_awaited_once()
