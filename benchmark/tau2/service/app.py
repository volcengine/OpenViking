#!/usr/bin/env python3
"""HTTP service exposing tau2 cases and rollout execution."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.tau2.train.case_loader import Tau2CaseLoader
from benchmark.tau2.train.rollout_executor import Tau2RolloutExecutor
from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    ExperienceSet,
    Rollout,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
)


class CasesQueryRequest(BaseModel):
    dataset: str = "tau2"
    domain: str
    split: str
    cursor: str | None = None
    limit: int = Field(default=100, gt=0)
    filters: dict[str, Any] = Field(default_factory=dict)


class RolloutExecuteRequest(BaseModel):
    case: dict[str, Any]
    policy_set: dict[str, Any]
    execution_context: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class _RolloutExecution:
    execution_id: str
    status: str
    created_at: float
    updated_at: float
    case_name: str
    rollout: Rollout | None = None
    error: str | None = None


class _RolloutExecutionStore:
    def __init__(self) -> None:
        self._executions: dict[str, _RolloutExecution] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, case_name: str) -> _RolloutExecution:
        now = time.time()
        execution = _RolloutExecution(
            execution_id=f"rollout_exec_{uuid4().hex}",
            status="running",
            created_at=now,
            updated_at=now,
            case_name=case_name,
        )
        async with self._lock:
            self._executions[execution.execution_id] = execution
        return execution

    async def get(self, execution_id: str) -> _RolloutExecution | None:
        async with self._lock:
            return self._executions.get(execution_id)

    async def mark_completed(self, execution_id: str, rollout: Rollout) -> None:
        await self._update(execution_id, status="completed", rollout=rollout)

    async def mark_failed(self, execution_id: str, error: str) -> None:
        await self._update(execution_id, status="failed", error=error)

    async def _update(self, execution_id: str, **changes: Any) -> None:
        async with self._lock:
            execution = self._executions[execution_id]
            for key, value in changes.items():
                setattr(execution, key, value)
            execution.updated_at = time.time()


def create_app(
    *,
    data_root: str | None = None,
    config_path: str | None = None,
    rollout_language: str = "default",
) -> FastAPI:
    if rollout_language not in {"default", "zh"}:
        raise ValueError("rollout_language must be 'default' or 'zh'")
    app = FastAPI(title="OpenViking Tau2 Rollout Service")
    app.state.data_root = data_root
    app.state.config_path = config_path
    app.state.rollout_language = rollout_language
    app.state.rollout_executions = _RolloutExecutionStore()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "tau2", "rollout_language": app.state.rollout_language}

    @app.post("/v1/cases/query")
    async def query_cases(request: CasesQueryRequest) -> dict[str, Any]:
        if request.dataset != "tau2":
            raise ValueError(f"Unsupported dataset: {request.dataset}")
        offset = int(request.cursor or "0")
        loader = Tau2CaseLoader(
            domain=request.domain,
            split=request.split,
            data_root=app.state.data_root,
        )
        all_cases = loader.load_cases()
        selected = all_cases[offset : offset + request.limit]
        next_offset = offset + len(selected)
        next_cursor = str(next_offset) if next_offset < len(all_cases) else None
        return {
            "cases": [_case_to_dict(case) for case in selected],
            "next_cursor": next_cursor,
        }

    @app.post("/v1/rollouts/execute")
    async def execute_rollout(request: RolloutExecuteRequest) -> dict[str, Any]:
        case = _case_from_dict(request.case)
        execution = await app.state.rollout_executions.create(case_name=case.name)
        asyncio.create_task(_run_rollout_execution(app, execution.execution_id, request))
        return _execution_to_dict(execution)

    @app.get("/v1/rollouts/executions/{execution_id}")
    async def get_rollout_execution(execution_id: str) -> dict[str, Any]:
        execution = await app.state.rollout_executions.get(execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail=f"Rollout execution not found: {execution_id}")
        return _execution_to_dict(execution)

    return app


async def _run_rollout_execution(
    app: FastAPI,
    execution_id: str,
    request: RolloutExecuteRequest,
) -> None:
    try:
        options = dict(request.options or {})
        executor = Tau2RolloutExecutor(
            config_path=options.get("config_path") or app.state.config_path,
            concurrency=1,
            keep_default_tools=bool(options.get("keep_default_tools", True)),
            max_iterations=int(options.get("max_iterations") or 30),
            rollout_language=str(options.get("rollout_language") or app.state.rollout_language),
        )
        rollouts = await executor.execute(
            [_case_from_dict(request.case)],
            _policy_set_from_dict(request.policy_set),
            ExecutionContext(
                policy_snapshot_id=str(request.execution_context["policy_snapshot_id"]),
                metadata=dict(request.execution_context.get("metadata") or {}),
            ),
        )
        await app.state.rollout_executions.mark_completed(execution_id, rollouts[0])
    except Exception as exc:
        await app.state.rollout_executions.mark_failed(execution_id, str(exc))


def _execution_to_dict(execution: _RolloutExecution) -> dict[str, Any]:
    data: dict[str, Any] = {
        "execution_id": execution.execution_id,
        "status": execution.status,
        "case_name": execution.case_name,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
        "error": execution.error,
    }
    if execution.rollout is not None:
        data["rollout"] = _rollout_to_dict(execution.rollout)
    return data


def _case_to_dict(case: Case) -> dict[str, Any]:
    return {
        "name": case.name,
        "task_signature": case.task_signature,
        "input": case.input,
        "rubric": {
            "name": case.rubric.name,
            "description": case.rubric.description,
            "criteria": [
                {
                    "name": criterion.name,
                    "description": criterion.description,
                    "required": criterion.required,
                    "weight": criterion.weight,
                    "metadata": criterion.metadata,
                }
                for criterion in case.rubric.criteria
            ],
            "metadata": case.rubric.metadata,
        },
        "metadata": case.metadata,
    }


def _case_from_dict(data: dict[str, Any]) -> Case:
    rubric = data["rubric"]
    return Case(
        name=data["name"],
        task_signature=data["task_signature"],
        input=dict(data.get("input") or {}),
        rubric=Rubric(
            name=rubric["name"],
            description=rubric.get("description", ""),
            criteria=[
                RubricCriterion(
                    name=item["name"],
                    description=item.get("description", ""),
                    required=bool(item.get("required", True)),
                    weight=float(item.get("weight", 1.0)),
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in rubric.get("criteria", [])
            ],
            metadata=dict(rubric.get("metadata") or {}),
        ),
        metadata=dict(data.get("metadata") or {}),
    )


def _policy_set_from_dict(data: dict[str, Any]) -> ExperienceSet:
    return ExperienceSet(
        root_uri=data["root_uri"],
        policies=[],
        metadata=dict(data.get("metadata") or {}),
    )


def _rollout_to_dict(rollout: Rollout) -> dict[str, Any]:
    return {
        "case": _case_to_dict(rollout.case),
        "messages": [message.to_dict() for message in rollout.messages],
        "policy_snapshot_id": rollout.policy_snapshot_id,
        "evaluation": _jsonable(_evaluation_to_dict(rollout.evaluation)),
        "metadata": _jsonable(rollout.metadata),
    }




def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(_jsonable(key)): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value

def _evaluation_to_dict(evaluation: RubricEvaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "passed": evaluation.passed,
        "score": evaluation.score,
        "criterion_results": [
            {
                "criterion_name": result.criterion_name,
                "passed": result.passed,
                "score": result.score,
                "feedback": result.feedback,
                "evidence": result.evidence,
                "metadata": result.metadata,
            }
            for result in evaluation.criterion_results
        ],
        "feedback": evaluation.feedback,
        "metadata": evaluation.metadata,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start tau2 rollout HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1944)
    parser.add_argument("--data-root", default=os.getenv("TAU2_DATA_ROOT"))
    parser.add_argument("--config", default=os.getenv("OPENVIKING_CONFIG_FILE"))
    parser.add_argument("--rollout-language", choices=["default", "zh"], default="default")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import uvicorn

    uvicorn.run(
        create_app(
            data_root=args.data_root,
            config_path=args.config,
            rollout_language=args.rollout_language,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
