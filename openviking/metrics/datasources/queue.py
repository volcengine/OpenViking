# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.metrics.core.base import ReadEnvelope
from openviking.storage.queuefs import get_queue_manager
from openviking_cli.utils import run_async

from .base import StateMetricDataSource


class QueuePipelineStateDataSource(StateMetricDataSource):
    """
    Read the current queue-pipeline status snapshot from the shared queue manager.

    The datasource translates the queue manager's async status check into the synchronous
    `ReadEnvelope` contract expected by state collectors.
    """

    def read_queue_status(self) -> ReadEnvelope[dict]:
        """
        Read the current queue-manager status snapshot used by queue-related collectors.

        The underlying queue manager API is async, so the datasource uses `run_async` to bridge
        into the synchronous collector refresh path.
        """
        queue_manager = get_queue_manager()
        return self.safe_read_async(
            lambda: queue_manager.check_status(),
            default={},
            runner=run_async,
        )
