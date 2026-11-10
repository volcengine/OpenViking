# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.import_policy import validate_import_policy
from openviking.server.resource_ingest import AddResourceRequest, ingest_resource_request
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg


@pytest.mark.parametrize(
    "strict,policy",
    [(False, "require_complete"), (True, "require_complete"), (False, "best_effort")],
)
def test_legal_combinations(strict, policy):
    request = AddResourceRequest(
        path="https://example.com/docs", strict=strict, import_policy=policy
    )
    assert request.import_policy == policy


def test_default_compatibility_and_conflict():
    request = AddResourceRequest(path="https://example.com/docs")
    assert not request.strict and request.import_policy == "require_complete"
    with pytest.raises(Exception, match="cannot be combined"):
        AddResourceRequest(
            path="https://example.com/docs", strict=True, import_policy="best_effort"
        )
    for value in ("strict", "best_effor", "", None, True):
        with pytest.raises(Exception, match="import_policy"):
            validate_import_policy(value)
    with pytest.raises(ValueError):
        AddResourceRequest(path="https://example.com/docs", import_polic="best_effort")


def test_queue_policy_roundtrip_and_legacy_default():
    message = AddResourceMsg(
        task_id="test",
        path="https://example.com/docs",
        root_uri="viking://resources/docs",
        account_id="test",
        user_id="test",
        role="user",
        import_policy="best_effort",
    )
    assert AddResourceMsg.from_dict(message.to_dict()).import_policy == "best_effort"
    data = message.to_dict()
    data.pop("import_policy")
    assert AddResourceMsg.from_dict(data).import_policy == "require_complete"


@pytest.mark.asyncio
async def test_request_forwards_policy():
    resources = SimpleNamespace(add_resource=AsyncMock(return_value={"status": "partial"}))
    await ingest_resource_request(
        AddResourceRequest(path="https://example.com/docs", import_policy="best_effort"),
        object(),
        resources=resources,
        config=None,
        wait=False,
        internal_task=False,
    )
    assert resources.add_resource.await_args.kwargs["import_policy"] == "best_effort"
