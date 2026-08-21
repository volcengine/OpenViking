# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.content import IndexRepairRequest, apply_index_repair
from openviking.server.routers.system import ConsistencyRequest
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(UserIdentifier("account", "user"), Role.ROOT)


def test_consistency_request_rejects_unknown_fields_and_invalid_limits() -> None:
    with pytest.raises(ValidationError):
        ConsistencyRequest(uri="viking://resources/demo", unexpected=True)
    with pytest.raises(ValidationError):
        ConsistencyRequest(uri="viking://resources/demo", limit=0)


async def test_repair_router_only_delegates_to_service(monkeypatch) -> None:
    apply = AsyncMock(return_value={"status": "dry_run"})
    monkeypatch.setattr(
        "openviking.server.routers.content.get_service",
        lambda: SimpleNamespace(apply_index_repair_plan=apply),
    )
    plan = {"root_uri": "viking://resources/demo", "plan_version": "index-repair/v1"}

    ctx = _ctx()
    response = await apply_index_repair(
        IndexRepairRequest(plan=plan, wait=False, dry_run=True),
        ctx,
    )

    assert response.result == {"status": "dry_run"}
    apply.assert_awaited_once_with(plan=plan, wait=False, dry_run=True, ctx=ctx)
