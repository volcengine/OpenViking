#!/usr/bin/env python3
"""ALFWorld CaseLoader for the OpenViking remote benchmark service."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openviking.session.train import Case, Rubric, RubricCriterion

TASKS = [
    "pick_and_place",
    "pick_two_obj_and_place",
    "look_at_obj_in_light",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
    "pick_clean_then_place_in_recep",
]

_SPLIT_ALIASES = {
    "train": "train",
    "dev": "eval_in_distribution",
    "valid_seen": "eval_in_distribution",
    "validation_seen": "eval_in_distribution",
    "eval_in_distribution": "eval_in_distribution",
    "eval_indistribution": "eval_in_distribution",
    "test": "eval_out_of_distribution",
    "valid_unseen": "eval_out_of_distribution",
    "validation_unseen": "eval_out_of_distribution",
    "eval_out_of_distribution": "eval_out_of_distribution",
    "eval_outofdistribution": "eval_out_of_distribution",
}


@dataclass(slots=True)
class AlfworldCaseLoader:
    """Load ALFWorld gamefiles as train-domain Cases.

    If explicit gamefiles (or discoverable files under ALFWORLD_DATA) are not available,
    this loader emits a finite set of pseudo environment-slot cases. That mirrors
    SkillOpt's lightweight ALFWorld item model while still letting the generic
    OpenViking remote service page through cases.
    """

    domain: str = "all"
    split: str = "test"
    batch_size: int | None = None
    data_root: str | None = None
    task_indices: list[int] | None = None
    gamefiles: list[str] | None = None
    case_count: int = 1
    allow_pseudo_cases: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    async def batches(self, context: Any = None) -> AsyncIterator[list[Case]]:
        del context
        cases = self.load_cases()
        size = self.batch_size or 1
        if size <= 0:
            raise ValueError("batch_size must be > 0")
        for start in range(0, len(cases), size):
            yield cases[start : start + size]

    def load_cases(self) -> list[Case]:
        entries = self.load_entries()
        return [self._case_from_entry(index, entry) for index, entry in entries]

    def load_entries(self) -> list[tuple[int, dict[str, Any]]]:
        entries = self._load_all_entries()
        if self.task_indices is None:
            return list(enumerate(entries))
        selected: list[tuple[int, dict[str, Any]]] = []
        for index in self.task_indices:
            if index < 0:
                raise ValueError("task_indices must be >= 0")
            try:
                selected.append((index, entries[index]))
            except IndexError as exc:
                raise ValueError(
                    f"task index out of range for split {self.split!r}: {index} "
                    f"(size={len(entries)})"
                ) from exc
        return selected

    def split_exists(self) -> bool:
        return bool(self._load_all_entries())

    def _load_all_entries(self) -> list[dict[str, Any]]:
        split = normalize_alfworld_split(self.split)
        explicit = self.gamefiles or _list_from_metadata(self.metadata, "gamefiles")
        if explicit:
            paths = [_resolve_gamefile(path, self.data_root) for path in explicit]
        else:
            paths = discover_alfworld_gamefiles(
                data_root=self.data_root,
                split=split,
                domain=self.domain,
            )
        if paths:
            return [
                {
                    "gamefile": path,
                    "eval_dataset": split,
                    "task_type": get_task_type(path),
                    "env_index": idx,
                    "source": "gamefile",
                }
                for idx, path in enumerate(paths)
            ]

        if not self.allow_pseudo_cases:
            return []

        count = int(self.case_count or 1)
        if count <= 0:
            raise ValueError("case_count must be > 0")
        return [
            {
                "gamefile": "",
                "eval_dataset": split,
                "task_type": normalize_alfworld_domain(self.domain),
                "env_index": idx,
                "source": "env_slot",
            }
            for idx in range(count)
        ]

    def _case_from_entry(self, task_no: int, entry: dict[str, Any]) -> Case:
        split = normalize_alfworld_split(self.split)
        gamefile = str(entry.get("gamefile") or "")
        task_type = str(entry.get("task_type") or get_task_type(gamefile))
        case_id = _case_id(task_no=task_no, gamefile=gamefile, task_type=task_type)
        return Case(
            name=f"alfworld_{split}_{case_id}",
            task_signature=f"alfworld:{split}:{task_type}:{case_id}",
            input={
                "dataset": "alfworld",
                "domain": normalize_alfworld_domain(self.domain),
                "split": self.split,
                "eval_dataset": split,
                "task_no": task_no,
                "task_type": task_type,
                "gamefile": gamefile,
                "data_root": self.data_root,
                "source": entry.get("source"),
            },
            rubric=Rubric(
                name=f"alfworld_{split}_{case_id}_rubric",
                description="ALFWorld episode must be completed successfully.",
                criteria=[
                    RubricCriterion(
                        name="alfworld_success",
                        description="The ALFWorld environment reports won=True before max steps.",
                        required=True,
                        weight=1.0,
                    )
                ],
            ),
            metadata={
                "source": "alfworld",
                "domain": normalize_alfworld_domain(self.domain),
                "split": self.split,
                "eval_dataset": split,
                "task_type": task_type,
                "gamefile": gamefile,
            },
        )


def normalize_alfworld_split(value: str) -> str:
    key = str(value or "test").strip().lower().replace("-", "_")
    try:
        return _SPLIT_ALIASES[key]
    except KeyError as exc:
        raise ValueError(
            "ALFWorld split must be train, test, eval_in_distribution, or eval_out_of_distribution"
        ) from exc


def normalize_alfworld_domain(value: str | None) -> str:
    domain = str(value or "all").strip().lower()
    if domain in {"", "all", "alfworld"}:
        return "all"
    if domain not in TASKS:
        raise ValueError(f"Unsupported ALFWorld domain/task type: {value!r}")
    return domain


def discover_alfworld_gamefiles(
    *,
    data_root: str | None = None,
    split: str = "eval_out_of_distribution",
    domain: str = "all",
) -> list[str]:
    root_text = data_root or os.getenv("ALFWORLD_DATA")
    if not root_text:
        return []
    root = Path(root_text).expanduser()
    if not root.exists():
        return []
    normalized_split = normalize_alfworld_split(split)
    normalized_domain = normalize_alfworld_domain(domain)
    candidates = set(root.rglob("game.tw-pddl"))
    candidates.update(root.rglob("*.tw-pddl"))
    paths: list[str] = []
    for path in sorted(candidates):
        text = path.as_posix()
        if not _path_matches_split(text, normalized_split):
            continue
        if normalized_domain != "all" and normalized_domain not in text:
            continue
        paths.append(str(path.resolve()))
    return paths


def get_task_type(gamefile: str) -> str:
    for task in TASKS:
        if task in str(gamefile):
            return task
    return "other"


def _path_matches_split(path: str, split: str) -> bool:
    normalized = path.lower().replace("-", "_")
    if split == "train":
        return "/train/" in normalized or normalized.endswith("/train")
    if split == "eval_in_distribution":
        return (
            "valid_seen" in normalized
            or "eval_in_distribution" in normalized
            or "eval_indistribution" in normalized
        )
    if split == "eval_out_of_distribution":
        return (
            "valid_unseen" in normalized
            or "eval_out_of_distribution" in normalized
            or "eval_outofdistribution" in normalized
        )
    return True


def _resolve_gamefile(path: str, data_root: str | None) -> str:
    expanded = os.path.expanduser(os.path.expandvars(str(path)))
    if os.path.isabs(expanded):
        return expanded
    root = data_root or os.getenv("ALFWORLD_DATA")
    if not root:
        return expanded
    return str((Path(root).expanduser() / expanded).resolve())


def _list_from_metadata(metadata: dict[str, Any], key: str) -> list[str] | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"{key} must be a string or list")


def _case_id(*, task_no: int, gamefile: str, task_type: str) -> str:
    if not gamefile:
        return f"env_{task_no:03d}"
    # The task directory name is stable and readable for normal ALFWorld layouts.
    parent = Path(gamefile).parent.name or task_type
    safe_parent = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in parent)
    return f"{task_no:05d}_{safe_parent}"
