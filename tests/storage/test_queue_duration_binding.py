# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Queue timing integration through the real RAGFS Python binding."""

import time

import pytest

from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig


@pytest.mark.asyncio
async def test_queuefs_dequeue_exposes_original_enqueue_timestamp(tmp_path):
    client = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="memory"))
    )
    queue = NamedQueue(client, "/queue", "TimingTest")
    before = time.time()

    await queue.enqueue({"value": 1})
    message = await queue.dequeue_raw()

    assert message is not None
    assert message["id"]
    assert message["data"] == '{"value": 1}'
    assert before <= float(message["timestamp"]) <= time.time()
