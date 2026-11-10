#!/usr/bin/env python3
"""Tau2-bench tool provider that exposes environment tools in OpenAI schema."""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .tau2_environment import Tau2BenchEnv


class Tau2BenchToolProvider:
    """Wrap Tau2BenchEnv tools into OpenAI tool schemas for MCP."""

    def __init__(self, domain: str, task_id: str, data_root: str | None = None):
        self.domain = domain
        self.task_id = task_id
        self.data_root = data_root
        self.env: Tau2BenchEnv | None = None
        self._openai_tools: list[dict[str, Any]] = []

    def reset(self) -> None:
        env = Tau2BenchEnv(self.domain, self.task_id)
        env.reset()
        self.env = env
        # Tau2BenchEnv.tool_schemas already includes tau2's native tools plus
        # communicate_with_user.
        self._openai_tools = list(env.tool_schemas)

    @property
    def user_query(self) -> str:
        if not self.env:
            return ""
        return self.env.user_query

    @property
    def policy(self) -> str:
        if not self.env:
            return ""
        return self.env.policy

    @property
    def ground_truth(self) -> str:
        if not self.env:
            return ""
        return self.env.ground_truth

    def list_openai_tools(self) -> Iterable[dict[str, Any]]:
        return self._openai_tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if not self.env:
            raise RuntimeError("Environment not initialized. Call reset() first.")
        normalized = name.replace("-", "_")
        return self.env.tool_call(normalized, arguments)


def load_task_id(data_split: str, task_no: int) -> tuple[str, str]:
    """Resolve task_id from split file and return (domain, task_id)."""
    domain, split = data_split.split("_", 1)
    data_root = os.getenv("TAU2_DATA_ROOT")
    if not data_root:
        raise RuntimeError(
            "TAU2_DATA_ROOT is not set. Point it at your tau2-bench data dir, e.g. "
            "export TAU2_DATA_ROOT=<tau2-bench>/data/tau2 (see setup_env.sh)."
        )
    domain_dir = os.path.join(data_root, "domains", domain)
    split_path = os.path.join(domain_dir, "split_tasks.json")
    if os.path.exists(split_path):
        with open(split_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        task_ids = data[split]
    else:
        tasks_path = os.path.join(domain_dir, "tasks.json")
        if not os.path.exists(tasks_path):
            raise FileNotFoundError(
                f"Neither split_tasks.json nor tasks.json found under: {domain_dir}"
            )
        with open(tasks_path, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        task_ids = [str(task["id"]) for task in tasks]
        split_at = max(1, len(task_ids) // 2)
        if split == "train":
            task_ids = task_ids[:split_at]
        elif split == "test":
            task_ids = task_ids[split_at:] if split_at < len(task_ids) else []
    task_id = task_ids[task_no]
    return domain, task_id
