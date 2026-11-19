# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


@pytest.mark.asyncio
async def test_disabled_semantic_processor_skips_before_resolving_vlm():
    resolver = SimpleNamespace(get_vlm=AsyncMock())
    processor = SemanticProcessor(vlm_resolver=resolver, disable_vlm=True)
    msg = SemanticMsg(
        uri="viking://resources/account/documents",
        context_type="resource",
        account_id="account",
        user_id="user",
        generation_trigger="parent_refresh",
    )

    with patch(
        "openviking.metrics.datasources.resource.ResourceIngestionEventDataSource.record_stage"
    ) as record_stage:
        result = await processor.on_dequeue(msg.to_dict())

    assert result.outcome is ProcessOutcome.SUCCESS
    resolver.get_vlm.assert_not_awaited()
    record_stage.assert_not_called()
