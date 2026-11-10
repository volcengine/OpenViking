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


def _load_tau2_task(domain: str, task_id: str):
    from tau2.registry import registry

    tasks = registry.get_tasks_loader(domain)()
    task_by_id = {str(task.id): task for task in tasks}
    try:
        return task_by_id[task_id]
    except KeyError as exc:
        raise ValueError(f"tau2 task not found domain={domain} task_id={task_id}") from exc


@dataclass(slots=True)
class Tau2CaseLoader:
    """Load tau2 split tasks as train-domain Cases."""

    domain: str
    split: str
    batch_size: int | None = None
    data_root: str | None = None
    task_indices: list[int] | None = None

    async def batches(self, context: Any = None) -> AsyncIterator[list[Case]]:
        del context
        task_ids = self.load_task_ids()
        size = self.batch_size or 1
        if size <= 0:
            raise ValueError("batch_size must be > 0")
        for start in range(0, len(task_ids), size):
            yield [
                self._case_from_task(task_no, task_id)
                for task_no, task_id in task_ids[start : start + size]
            ]

    def load_cases(self) -> list[Case]:
        task_ids = self.load_task_ids()
        return [self._case_from_task(task_no, task_id) for task_no, task_id in task_ids]

    def load_task_ids(self) -> list[tuple[int, str]]:
        data = _load_split_tasks(self.domain, self.data_root)
        values = data.get(self.split)
        if not isinstance(values, list):
            return []
        task_ids = [(task_no, str(item)) for task_no, item in enumerate(values)]
        if self.task_indices is None:
            return task_ids
        selected: list[tuple[int, str]] = []
        for index in self.task_indices:
            if index < 0:
                raise ValueError("task_indices must be >= 0")
            try:
                selected.append(task_ids[index])
            except IndexError as exc:
                raise ValueError(
                    f"task index out of range for split {self.split!r}: {index} "
                    f"(size={len(task_ids)})"
                ) from exc
        return selected

    def split_exists(self) -> bool:
        data = _load_split_tasks(self.domain, self.data_root)
        values = data.get(self.split)
        return isinstance(values, list) and bool(values)

    def _case_from_task(self, task_no: int, task_id: str) -> Case:
        task = _load_tau2_task(self.domain, task_id)
        policy = ""
        user_query = str(task.user_scenario)
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
                "user_query": user_query,
                "policy": policy,
            },
            rubric=Rubric(
                name=f"tau2_{data_split}_{task_no}_rubric",
                description="Tau2 task reward must reach 1.0.",
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
    domain_dir = Path(root).expanduser() / "domains" / domain
    split_path = domain_dir / "split_tasks.json"
    if split_path.exists():
        return json.loads(split_path.read_text(encoding="utf-8"))

    tasks_path = domain_dir / "tasks.json"
    if not tasks_path.exists():
        raise FileNotFoundError(
            f"Neither split_tasks.json nor tasks.json found under: {domain_dir}"
        )
    return _derive_split_tasks_from_tasks_json(tasks_path)


def _derive_split_tasks_from_tasks_json(tasks_path: Path) -> dict[str, list[str]]:
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError(f"tasks.json must be a list: {tasks_path}")
    task_ids = [str(task.get("id")) for task in tasks if isinstance(task, dict) and "id" in task]
    if not task_ids:
        raise ValueError(f"tasks.json contains no task ids: {tasks_path}")
    split_at = max(1, len(task_ids) // 2)
    if split_at >= len(task_ids):
        return {"train": task_ids, "test": []}
    return {"train": task_ids[:split_at], "test": task_ids[split_at:]}
