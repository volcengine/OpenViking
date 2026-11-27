# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import ReindexExecutor
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_reindex_rejects_file_only_namespace_before_task_creation(monkeypatch):
    def unexpected_tracker():
        pytest.fail("file-only namespace must not create reindex tasks")

    monkeypatch.setattr("openviking.service.reindex_executor.get_task_tracker", unexpected_tracker)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)

    with pytest.raises(InvalidArgumentError, match="reindex is not supported"):
        await ReindexExecutor().execute(
            uri="viking://system/memory/memory_templates/custom/demo",
            mode="full",
            wait=False,
            ctx=ctx,
        )
