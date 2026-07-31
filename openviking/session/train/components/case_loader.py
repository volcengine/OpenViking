# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Case loader implementations for session training."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from openviking.session.train.domain import Case
from openviking.session.train.interfaces import CaseLoader
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


@dataclass(slots=True)
class TrialCaseLoader:
    """Expand every base case into N trial cases in each emitted batch."""

    base_loader: CaseLoader
    trial_count: int
    trial_input_key: str = "trial"
    trial_count_input_key: str = "trial_count"
    original_case_name_input_key: str = "original_case_name"
    trial_name_template: str = "{case_name}_t{trial_index}"
    trial_task_signature_template: str = "{task_signature}:trial:{trial_index}"
    extra_input: dict[str, Any] = field(default_factory=dict)
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.trial_count <= 0:
            raise ValueError("trial_count must be > 0")

    async def batches(self, context: Any) -> AsyncIterator[list[Case]]:
        async for cases in self.base_loader.batches(context):
            expanded: list[Case] = []
            for trial_index in range(self.trial_count):
                expanded.extend(self._trial_case(case, trial_index) for case in cases)
            yield expanded

    async def split_exists(self) -> bool:
        split_exists = getattr(self.base_loader, "split_exists", None)
        if split_exists is None:
            return True
        return bool(await split_exists())

    def _trial_case(self, case: Case, trial_index: int) -> Case:
        trial_values = {
            self.trial_input_key: trial_index,
            self.trial_count_input_key: self.trial_count,
            self.original_case_name_input_key: case.name,
        }
        format_values = {
            "case_name": case.name,
            "task_signature": case.task_signature,
            "trial_index": trial_index,
            "trial_count": self.trial_count,
        }
        return Case(
            name=self.trial_name_template.format(**format_values),
            task_signature=self.trial_task_signature_template.format(**format_values),
            input={
                **dict(case.input),
                **trial_values,
                **dict(self.extra_input),
            },
            rubric=case.rubric,
            metadata={
                **dict(case.metadata),
                **trial_values,
                **dict(self.extra_metadata),
            },
        )


def make_trial_case_loader(
    base_loader: CaseLoader,
    trial_count: int,
    *,
    trial_input_key: str = "trial",
    trial_count_input_key: str | None = None,
    original_case_name_input_key: str = "original_case_name",
) -> TrialCaseLoader:
    return TrialCaseLoader(
        base_loader=base_loader,
        trial_count=trial_count,
        trial_input_key=trial_input_key,
        trial_count_input_key=trial_count_input_key or f"{trial_input_key}_count",
        original_case_name_input_key=original_case_name_input_key,
    )
