# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import ReindexExecutor
from openviking.storage.index_consistency import build_index_expectations
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_file_only_namespace_skips_index_checks_and_rejects_reindex(monkeypatch):
    def unexpected_tracker():
        pytest.fail("file-only namespace must not create reindex tasks")

    monkeypatch.setattr("openviking.service.reindex_executor.get_task_tracker", unexpected_tracker)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)

    uri = "viking://system/memory/memory_templates/custom/demo"
    fs = AsyncMock()
    fs.exists.return_value = True
    fs.read_file.return_value = "description"
    expectations = await build_index_expectations(
        fs, uri, [{"rel_path": "template.md", "isDir": False, "size": 11}], ctx
    )
    assert expectations == ()

    with pytest.raises(InvalidArgumentError, match="reindex is not supported"):
        await ReindexExecutor().execute(
            uri=uri,
            mode="full",
            wait=False,
            ctx=ctx,
        )
