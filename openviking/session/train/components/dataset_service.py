# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generic HTTP service host for remote benchmark datasets."""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    CriterionResult,
    Experience,
    ExperienceSet,
    Rollout,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
)

CaseLoaderFactory = Callable[[str, str, str, dict[str, Any]], Any]
RolloutExecutorFactory = Callable[[dict[str, Any]], Any]
logger = logging.getLogger(__name__)
_rollout_worker_state = threading.local()

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "openviking_api_key",
        "root_api_key",
        "authorization",
        "x-api-key",
        "token",
        "secret",
    }
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(openviking_api_key|root_api_key|api_key|x-api-key|authorization|token|secret)"
    r"(\s*[:=]\s*)"
    r"(['\"]?)([^'\"\s,}]+)(\3)"
)


def _redact_sensitive_text(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        return (
            f"{match.group(1)}{match.group(2)}"
            f"{match.group(3)}<redacted>{match.group(5)}"
        )

    return _SENSITIVE_TEXT_RE.sub(_replace, text)


def redact_sensitive(value: Any) -> Any:
    """Return a copy with known credential fields redacted for logs/errors."""
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in SENSITIVE_FIELD_NAMES or key_text.endswith("_api_key"):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


class CasesQueryRequest(BaseModel):
    dataset: str
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
class RolloutExecution:
    execution_id: str
    status: str
    created_at: float
    updated_at: float
    case_name: str
    finished_at: float | None = None
    # ``rollout`` is only populated on copies returned by ``get()`` for
    # completed executions. Filesystem-backed records never keep Rollout
    # objects alive across requests, so large payloads (messages, tool outputs,
    # prompts, reasoning) are reloaded from disk on each poll and released as
    # soon as the caller returns.
    rollout: Rollout | None = None
    error: str | None = None


class RolloutExecutionStore:
    """Filesystem-backed execution store with zero in-memory state.

    Execution state is represented entirely by files under a root spool
    directory. The root directory is created under ``tempfile.gettempdir()`` by
    default so it is automatically cleared on service restart / host reboot;
    there is no TTL or eviction because no payloads are kept in memory beyond
    the scope of a single request.

    Directory layout::

        <root>/
          running/<execution_id>.json   # {status, created_at, updated_at, case_name}
          completed/<execution_id>.json # {status, created_at, updated_at, finished_at, case_name, rollout: {...}}
          failed/<execution_id>.json    # {status, created_at, updated_at, finished_at, case_name, error}
    """

    _RUNNING = "running"
    _COMPLETED = "completed"
    _FAILED = "failed"

    def __init__(self, *, spool_dir: Path | None = None) -> None:
        if spool_dir is None:
            # Per-process temporary directory. Each service process gets its own
            # spool root; the directory is removed on process exit via atexit so
            # disk usage does not grow across runs. Concurrent service instances
            # use separate directories (different mkdtemp suffixes) and do not
            # collide.
            spool_dir = Path(tempfile.mkdtemp(prefix="ov_rollout_spool_"))
            self._owns_spool_dir = True
        else:
            spool_dir = spool_dir.expanduser().resolve()
            self._owns_spool_dir = False
        spool_dir = spool_dir.expanduser().resolve()
        self._spool_dir = spool_dir
        for sub in (self._RUNNING, self._COMPLETED, self._FAILED):
            (spool_dir / sub).mkdir(parents=True, exist_ok=True)
        if self._owns_spool_dir:
            atexit.register(_cleanup_spool_dir, self._spool_dir)

    def _path(self, status: str, execution_id: str) -> Path:
        return self._spool_dir / status / f"{execution_id}.json"

    @staticmethod
    def _read_meta(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    async def create(self, *, case_name: str) -> RolloutExecution:
        # File creation is the single source of truth. Use a lock-free
        # atomic-write pattern; POSIX rename is atomic for same-directory files.
        now = time.time()
        execution_id = f"rollout_exec_{uuid4().hex}"
        meta = {
            "execution_id": execution_id,
            "status": self._RUNNING,
            "created_at": now,
            "updated_at": now,
            "case_name": case_name,
        }
        target = self._path(self._RUNNING, execution_id)
        _atomic_write_json(target, meta)
        return RolloutExecution(
            execution_id=execution_id,
            status=self._RUNNING,
            created_at=now,
            updated_at=now,
            case_name=case_name,
        )

    async def get(self, execution_id: str) -> RolloutExecution | None:
        for status in (self._COMPLETED, self._FAILED, self._RUNNING):
            data = self._read_meta(self._path(status, execution_id))
            if data is None:
                continue
            rollout: Rollout | None = None
            if status == self._COMPLETED:
                rollout_data = data.get("rollout")
                if isinstance(rollout_data, dict):
                    rollout = rollout_from_dict(rollout_data)
            return RolloutExecution(
                execution_id=data["execution_id"],
                status=status,
                created_at=float(data.get("created_at", 0.0)),
                updated_at=float(data.get("updated_at", 0.0)),
                case_name=data.get("case_name", ""),
                finished_at=(
                    float(data["finished_at"]) if data.get("finished_at") is not None else None
                ),
                rollout=rollout,
                error=data.get("error"),
            )
        return None

    async def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for status in (self._RUNNING, self._COMPLETED, self._FAILED):
            counts[status] = sum(
                1 for p in (self._spool_dir / status).iterdir() if p.suffix == ".json"
            )
        return counts

    async def mark_completed(self, execution_id: str, rollout: Rollout) -> None:
        now = time.time()
        running_path = self._path(self._RUNNING, execution_id)
        meta = self._read_meta(running_path)
        # If the running record is gone (e.g. the service restarted while a task
        # was in flight, or a prior failure already wrote a terminal record),
        # fall back to a synthetic metadata skeleton so the completed payload
        # is still persisted instead of raising a 500.
        if meta is None:
            completed_existing = self._read_meta(self._path(self._COMPLETED, execution_id))
            failed_existing = self._read_meta(self._path(self._FAILED, execution_id))
            existing = completed_existing or failed_existing or {}
            meta = {
                "execution_id": execution_id,
                "status": self._RUNNING,
                "created_at": existing.get("created_at", now),
                "updated_at": existing.get("updated_at", now),
                "case_name": existing.get("case_name", ""),
            }
        payload = {
            **meta,
            "status": self._COMPLETED,
            "updated_at": now,
            "finished_at": now,
            "rollout": rollout_to_dict(rollout),
        }
        completed_path = self._path(self._COMPLETED, execution_id)
        _atomic_write_json(completed_path, payload)
        try:
            running_path.unlink()
        except FileNotFoundError:
            pass

    async def mark_failed(self, execution_id: str, error: str) -> None:
        now = time.time()
        running_path = self._path(self._RUNNING, execution_id)
        meta = self._read_meta(running_path)
        if meta is None:
            completed_existing = self._read_meta(self._path(self._COMPLETED, execution_id))
            failed_existing = self._read_meta(self._path(self._FAILED, execution_id))
            existing = completed_existing or failed_existing or {}
            # Already in a terminal state: nothing to do.
            if completed_existing is not None or failed_existing is not None:
                return
            meta = {
                "execution_id": execution_id,
                "status": self._RUNNING,
                "created_at": existing.get("created_at", now),
                "updated_at": existing.get("updated_at", now),
                "case_name": existing.get("case_name", ""),
            }
        payload = {
            **meta,
            "status": self._FAILED,
            "updated_at": now,
            "finished_at": now,
            "error": str(redact_sensitive({"error": error})["error"]),
        }
        failed_path = self._path(self._FAILED, execution_id)
        _atomic_write_json(failed_path, payload)
        try:
            running_path.unlink()
        except FileNotFoundError:
            pass

    @property
    def spool_dir(self) -> Path:
        return self._spool_dir


def _cleanup_spool_dir(path: Path) -> None:
    """Best-effort recursive removal of the per-process spool directory on exit."""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via temp-file + rename so readers never see partial writes."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, default=str)
    tmp_path.replace(path)


def create_dataset_service_app(
    *,
    service_name: str,
    make_case_loader: CaseLoaderFactory,
    make_rollout_executor: RolloutExecutorFactory,
    max_rollout_concurrency: int | None = None,
    rollout_thread_workers: int | None = None,
) -> FastAPI:
    """Create a generic remote dataset service from train framework components."""

    if max_rollout_concurrency is not None and max_rollout_concurrency <= 0:
        raise ValueError("max_rollout_concurrency must be > 0")
    if rollout_thread_workers is not None and rollout_thread_workers <= 0:
        raise ValueError("rollout_thread_workers must be > 0")

    app = FastAPI(title=f"OpenViking {service_name} Dataset Service")
    app.state.service_name = service_name
    app.state.make_case_loader = make_case_loader
    app.state.make_rollout_executor = make_rollout_executor
    app.state.rollout_executions = RolloutExecutionStore()
    app.state.max_rollout_concurrency = max_rollout_concurrency
    app.state.rollout_semaphore = (
        asyncio.Semaphore(max_rollout_concurrency) if max_rollout_concurrency is not None else None
    )
    app.state.rollout_thread_workers = rollout_thread_workers
    app.state.rollout_thread_pool = (
        ThreadPoolExecutor(
            max_workers=rollout_thread_workers,
            thread_name_prefix=f"{service_name}-rollout",
        )
        if rollout_thread_workers is not None
        else None
    )

    @app.on_event("shutdown")
    async def shutdown_rollout_thread_pool() -> None:
        pool = app.state.rollout_thread_pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": app.state.service_name,
            "max_rollout_concurrency": app.state.max_rollout_concurrency,
            "rollout_thread_workers": app.state.rollout_thread_workers,
            "rollout_executions": await app.state.rollout_executions.count_by_status(),
        }

    @app.post("/v1/cases/query")
    async def query_cases(request: CasesQueryRequest) -> dict[str, Any]:
        loader = app.state.make_case_loader(
            request.dataset,
            request.domain,
            request.split,
            dict(request.filters or {}),
        )
        cases = await _load_case_page(
            loader,
            cursor=request.cursor,
            limit=request.limit,
        )
        next_offset = int(request.cursor or "0") + len(cases)
        next_cursor = str(next_offset) if len(cases) >= request.limit else None
        return {
            "cases": [case_to_dict(case) for case in cases],
            "next_cursor": next_cursor,
        }

    @app.post("/v1/rollouts/execute")
    async def execute_rollout(request: RolloutExecuteRequest) -> dict[str, Any]:
        case = case_from_dict(request.case)
        execution = await app.state.rollout_executions.create(case_name=case.name)
        asyncio.create_task(_run_rollout_execution(app, execution.execution_id, request))
        return execution_to_dict(execution)

    @app.get("/v1/rollouts/executions/{execution_id}")
    async def get_rollout_execution(execution_id: str) -> dict[str, Any]:
        execution = await app.state.rollout_executions.get(execution_id)
        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Rollout execution not found: {execution_id}",
            )
        return execution_to_dict(execution)

    return app


async def _run_rollout_execution(
    app: FastAPI,
    execution_id: str,
    request: RolloutExecuteRequest,
) -> None:
    case = case_from_dict(request.case)
    try:
        semaphore = app.state.rollout_semaphore
        if semaphore is None:
            rollout = await _execute_rollout_request_hosted(app, request, case)
        else:
            async with semaphore:
                rollout = await _execute_rollout_request_hosted(app, request, case)
        await app.state.rollout_executions.mark_completed(execution_id, rollout)
    except Exception as exc:
        logger.exception(
            "rollout execution failed execution_id=%s case=%s",
            execution_id,
            case.name,
        )
        try:
            await app.state.rollout_executions.mark_failed(
                execution_id,
                str(redact_sensitive({"error": str(exc)})["error"]),
            )
        except Exception:
            # Last-ditch: do not let failures in the failure-recording path
            # raise into the event loop (the task was already fire-and-forget).
            logger.exception(
                "rollout execution mark_failed itself failed execution_id=%s",
                execution_id,
            )


async def _execute_rollout_request_hosted(
    app: FastAPI,
    request: RolloutExecuteRequest,
    case: Case,
) -> Rollout:
    """Execute one rollout either on the ASGI loop or in the service thread pool.

    Some benchmark backends (notably full agent-loop implementations) include
    blocking synchronous sections that can starve the uvicorn event loop when
    many rollouts are hosted by the same process.  When a rollout thread pool is
    configured, the whole rollout coroutine is driven by a fresh event loop in a
    worker thread so request/polling endpoints remain responsive.
    """

    pool = getattr(app.state, "rollout_thread_pool", None)
    if pool is None:
        return await _execute_rollout_request(app, request, case)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        pool,
        _execute_rollout_request_in_thread,
        app,
        request,
        case,
    )


def _execute_rollout_request_in_thread(
    app: FastAPI,
    request: RolloutExecuteRequest,
    case: Case,
) -> Rollout:
    loop = getattr(_rollout_worker_state, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _rollout_worker_state.loop = loop
        asyncio.set_event_loop(loop)
    # Keep the worker-thread event loop alive across rollout executions.  Some
    # provider stacks schedule async client cleanup tasks (e.g. httpx.aclose)
    # late in the request lifecycle; using asyncio.run() here would close the
    # loop immediately after each rollout and those cleanup tasks can then raise
    # "RuntimeError: Event loop is closed".
    try:
        result = loop.run_until_complete(_execute_rollout_request(app, request, case))
        loop.run_until_complete(asyncio.sleep(0))
        return result
    finally:
        asyncio.set_event_loop(loop)


async def _execute_rollout_request(
    app: FastAPI,
    request: RolloutExecuteRequest,
    case: Case,
) -> Rollout:
    options = dict(request.options or {})
    executor = app.state.make_rollout_executor(options)
    rollouts = await executor.execute(
        [case],
        policy_set_from_dict(request.policy_set),
        ExecutionContext(
            policy_snapshot_id=str(request.execution_context["policy_snapshot_id"]),
            metadata=dict(request.execution_context.get("metadata") or {}),
        ),
    )
    return rollouts[0]


async def _load_case_page(loader: Any, *, cursor: str | None, limit: int) -> list[Case]:
    offset = int(cursor or "0")
    selected: list[Case] = []
    seen = 0
    async for batch in loader.batches(None):
        for case in batch:
            if seen < offset:
                seen += 1
                continue
            if len(selected) >= limit:
                return selected
            selected.append(case)
            seen += 1
    return selected


def execution_to_dict(execution: RolloutExecution) -> dict[str, Any]:
    data: dict[str, Any] = {
        "execution_id": execution.execution_id,
        "status": execution.status,
        "case_name": execution.case_name,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
        "error": execution.error,
    }
    if execution.finished_at is not None:
        data["duration_ms"] = round((execution.finished_at - execution.created_at) * 1000.0, 2)
    if execution.rollout is not None:
        data["rollout"] = rollout_to_dict(execution.rollout)
    return data


def case_to_dict(case: Case) -> dict[str, Any]:
    return {
        "name": case.name,
        "task_signature": case.task_signature,
        "input": jsonable(case.input),
        "rubric": {
            "name": case.rubric.name,
            "description": case.rubric.description,
            "criteria": [
                {
                    "name": criterion.name,
                    "description": criterion.description,
                    "required": criterion.required,
                    "weight": criterion.weight,
                    "metadata": jsonable(criterion.metadata),
                }
                for criterion in case.rubric.criteria
            ],
            "metadata": jsonable(case.rubric.metadata),
        },
        "metadata": jsonable(case.metadata),
    }


def case_from_dict(data: dict[str, Any]) -> Case:
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


def policy_set_from_dict(data: dict[str, Any]) -> ExperienceSet:
    return ExperienceSet(
        root_uri=data["root_uri"],
        policies=[
            Experience(
                name=item["name"],
                uri=item["uri"],
                version=int(item["version"]),
                status=item["status"],
                content=item["content"],
                metadata=dict(item.get("metadata") or {}),
            )
            for item in data.get("policies", [])
        ],
        metadata=dict(data.get("metadata") or {}),
    )


def policy_set_to_dict(policy_set: ExperienceSet) -> dict[str, Any]:
    return {
        "root_uri": policy_set.root_uri,
        "policies": [
            {
                "name": item.name,
                "uri": item.uri,
                "version": item.version,
                "status": item.status,
                "content": item.content,
                "metadata": jsonable(item.metadata),
            }
            for item in policy_set.policies
        ],
        "metadata": jsonable(policy_set.metadata),
    }


def rollout_to_dict(rollout: Rollout) -> dict[str, Any]:
    return {
        "case": case_to_dict(rollout.case),
        "messages": [message.to_dict() for message in rollout.messages],
        "policy_snapshot_id": rollout.policy_snapshot_id,
        "evaluation": jsonable(evaluation_to_dict(rollout.evaluation)),
        "metadata": jsonable(rollout.metadata),
    }


def rollout_from_dict(data: dict[str, Any]) -> Rollout:
    """Inverse of ``rollout_to_dict``. Used when reloading spooled Rollout payloads."""
    from openviking.message import Message

    return Rollout(
        case=case_from_dict(data["case"]),
        messages=[
            Message.from_dict(_message_dict_with_defaults(item, index))
            for index, item in enumerate(data.get("messages", []))
        ],
        policy_snapshot_id=data["policy_snapshot_id"],
        evaluation=evaluation_from_dict(data.get("evaluation")),
        metadata=dict(data.get("metadata") or {}),
    )


def _message_dict_with_defaults(data: dict[str, Any], index: int) -> dict[str, Any]:
    """Accept lightweight remote message payloads that omit OpenViking-only ids."""
    item = dict(data)
    item.setdefault("id", f"remote_message_{index}")
    return item


def evaluation_from_dict(data: dict[str, Any] | None) -> RubricEvaluation | None:
    if data is None:
        return None
    return RubricEvaluation(
        passed=bool(data.get("passed")),
        score=float(data.get("score") or 0.0),
        criterion_results=[
            CriterionResult(
                criterion_name=item.get("criterion_name", "unknown"),
                passed=bool(item.get("passed")),
                score=float(item.get("score") or 0.0),
                feedback=[str(value) for value in item.get("feedback", [])],
                evidence=[str(value) for value in item.get("evidence", [])],
                metadata=dict(item.get("metadata") or {}),
            )
            for item in data.get("criterion_results", [])
        ],
        feedback=[str(value) for value in data.get("feedback", [])],
        metadata=dict(data.get("metadata") or {}),
    )


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(jsonable(key)): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    return value


def evaluation_to_dict(evaluation: RubricEvaluation | None) -> dict[str, Any] | None:
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
                "metadata": jsonable(result.metadata),
            }
            for result in evaluation.criterion_results
        ],
        "feedback": evaluation.feedback,
        "metadata": jsonable(evaluation.metadata),
    }
