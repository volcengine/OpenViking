# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_queue import SemanticQueue, is_semantic_coalesce_stale


def _message(key: str) -> SemanticMsg:
    return SemanticMsg(
        uri="viking://resources/docs",
        context_type="resource",
        coalesce_key=key,
    )


@pytest.mark.asyncio
async def test_failed_first_enqueue_does_not_advance_coalesce_version():
    key = build_semantic_coalesce_key(
        context_type="resource", uri=f"viking://resources/docs/{uuid4().hex}"
    )
    queue = SemanticQueue(MagicMock(), "/queue", "semantic")

    with patch.object(NamedQueue, "enqueue", AsyncMock(side_effect=RuntimeError("queue down"))):
        with pytest.raises(RuntimeError, match="queue down"):
            await queue.enqueue(_message(key))

    assert is_semantic_coalesce_stale(key, 1) is False


@pytest.mark.asyncio
async def test_failed_enqueue_keeps_previous_message_live():
    key = build_semantic_coalesce_key(
        context_type="resource", uri=f"viking://resources/docs/{uuid4().hex}"
    )
    queue = SemanticQueue(MagicMock(), "/queue", "semantic")

    with patch.object(NamedQueue, "enqueue", AsyncMock(return_value="queued-1")):
        await queue.enqueue(_message(key))

    with patch.object(NamedQueue, "enqueue", AsyncMock(side_effect=RuntimeError("queue down"))):
        with pytest.raises(RuntimeError, match="queue down"):
            await queue.enqueue(_message(key))

    assert is_semantic_coalesce_stale(key, 1) is False
