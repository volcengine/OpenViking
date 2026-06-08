#!/usr/bin/env python3
"""Tau2 task CaseLoader for OpenViking batch policy training."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking.session.train import Case, Rubric, RubricCriterion


def _tool_provider_cls():
    from benchmark.tau2.common.tau2_env.tau2_tool_provider import Tau2BenchToolProvider

    return Tau2BenchToolProvider


@dataclass(slots=True)
class Tau2CaseLoader:
    """Load tau2 split tasks as train-domain Cases."""

    domain: str
    split: str
    batch_size: int | None = None
    data_root: str | None = None

    async def batches(self, context: Any = None) -> AsyncIterator[list[Case]]:
        del context
        cases = self.load_cases()
        size = self.batch_size or len(cases) or 1
        if size <= 0:
            raise ValueError("batch_size must be > 0")
        for start in range(0, len(cases), size):
            yield cases[start : start + size]

    def load_cases(self) -> list[Case]:
        task_ids = self.load_task_ids()
        return [self._case_from_task(task_no, task_id) for task_no, task_id in enumerate(task_ids)]

    def load_task_ids(self) -> list[str]:
        data = _load_split_tasks(self.domain, self.data_root)
        values = data.get(self.split)
        if not isinstance(values, list):
            return []
        return [str(item) for item in values]

    def split_exists(self) -> bool:
        data = _load_split_tasks(self.domain, self.data_root)
        values = data.get(self.split)
        return isinstance(values, list) and bool(values)

    def _case_from_task(self, task_no: int, task_id: str) -> Case:
        Tau2BenchToolProvider = _tool_provider_cls()
        provider = Tau2BenchToolProvider(self.domain, task_id, data_root=self.data_root)
        provider.reset()
        data_split = f"{self.domain}_{self.split}"
        return Case(
            name=f"tau2_{data_split}_{task_no}",
            task_signature=f"tau2:{self.domain}:{self.split}:{task_id}",
            input={
                "domain": self.domain,
                "split": self.split,
                "data_split": data_split,
                "task_no": task_no,
                "task_id": task_id,
                "data_root": self.data_root,
                "user_query": provider.user_query,
                "policy": provider.policy,
                "ground_truth": provider.ground_truth,
            },
            rubric=Rubric(
                name=f"tau2_{data_split}_{task_no}_rubric",
                description=provider.ground_truth,
                criteria=[
                    RubricCriterion(
                        name="tau2_reward",
                        description="The tau2 environment reward is 1.0.",
                        required=True,
                        weight=1.0,
                    )
                ],
            ),
            metadata={"source": "tau2", "domain": self.domain, "split": self.split},
        )


def _load_split_tasks(domain: str, data_root: str | None = None) -> dict[str, Any]:
    root = data_root or os.getenv("TAU2_DATA_ROOT")
    if not root:
        raise RuntimeError(
            "TAU2_DATA_ROOT is not set. Point it at your tau2-bench data dir, e.g. "
            "export TAU2_DATA_ROOT=<tau2-bench>/data/tau2."
        )
    path = Path(root).expanduser() / "domains" / domain / "split_tasks.json"
    if not path.exists():
        raise FileNotFoundError(f"Split file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
