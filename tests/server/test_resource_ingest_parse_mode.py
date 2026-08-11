# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""parse_mode propagation through shared temp-upload ingestion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server import resource_ingest
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_ingest_temp_upload_forwards_no_split(monkeypatch: pytest.MonkeyPatch):
    resolved = SimpleNamespace(
        local_path="/tmp/upload.bin",
        original_filename="upload.bin",
        cleanup=AsyncMock(),
    )
    store = SimpleNamespace(
        resolve_for_consume=AsyncMock(return_value=resolved),
        mark_consumed=AsyncMock(),
        mark_failed=AsyncMock(),
    )
    add_resource = AsyncMock(
        return_value={"status": "success", "root_uri": "viking://resources/upload"}
    )
    monkeypatch.setattr(
        resource_ingest,
        "get_service",
        lambda: SimpleNamespace(resources=SimpleNamespace(add_resource=add_resource)),
    )
    ctx = RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )

    await resource_ingest.ingest_temp_upload(
        store,
        "upload-id",
        ctx,
        parse_mode="no_split",
    )

    assert add_resource.await_args.kwargs["args"] == {"parse_mode": "no_split"}
    assert "parse_mode" not in add_resource.await_args.kwargs
