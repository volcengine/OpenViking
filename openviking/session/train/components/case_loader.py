# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Case loader implementations for session training."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openviking.session.train.domain import Case
from openviking.telemetry import tracer


@dataclass(slots=True)
class ListCaseLoader:
    """Simple in-memory CaseLoader implementation."""

    cases: list[Case]
    batch_size: int | None = None

    @tracer("train.case_loader.list.batches", ignore_result=True, ignore_args=True)
    async def batches(self, context: Any) -> AsyncIterator[list[Case]]:
        del context
        batch_size = self.batch_size or len(self.cases) or 1
        for start in range(0, len(self.cases), batch_size):
            yield list(self.cases[start : start + batch_size])
