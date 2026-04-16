# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.metrics.core.base import ReadEnvelope
from openviking.service.task_tracker import get_task_tracker

from .base import StateMetricDataSource


class TaskStateDataSource(StateMetricDataSource):
    """
    Read task-count snapshots from the shared task tracker service.

    This datasource exposes grouped task totals as a point-in-time state read so task-related
    collectors can publish gauges without depending on tracker internals.
    """

    def read_task_counts(self) -> ReadEnvelope[dict]:
        """
        Read the current task counts grouped by task type from the global task tracker.

        The read is best-effort and returns a `ReadEnvelope` so collectors can export fallback or
        stale values if the tracker lookup fails.
        """
        tracker = get_task_tracker()
        return self.safe_read(tracker.snapshot_counts_by_type, default={})
