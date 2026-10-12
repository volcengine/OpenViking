# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from .base import EventMetricDataSource


class ModelRetryEventDataSource(EventMetricDataSource):
    """Publish normalized retry-owner events to the metrics event bus."""

    @classmethod
    def record(cls, event: str, **fields) -> None:
        cls._emit(f"model_retry.{event}", fields)
