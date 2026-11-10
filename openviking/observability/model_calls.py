# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Correlate provider attempts and semantic waits without logging request bodies."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator
from uuid import uuid4

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
_node: ContextVar[tuple[str, str, str] | None] = ContextVar("model_call_node", default=None)


@contextmanager
def bind_model_call_node(account_id: str, uri: str, phase: str) -> Iterator[None]:
    token = _node.set((account_id, uri, phase))
    try:
        yield
    finally:
        _node.reset(token)


def diagnostic_fields() -> dict[str, Any]:
    from openviking.observability.context import get_root_observability_context
    from openviking.service.task_work_index import get_task_context

    task = get_task_context()
    root = get_root_observability_context()
    node = _node.get()
    return {
        "task_id": task.task_id if task else "",
        "work_id": task.work_id if task else "",
        "account_id": node[0]
        if node
        else task.account_id
        if task
        else root.account_id
        if root
        else "",
        "user_id": task.user_id if task else root.user_id if root else "",
        "uri": node[1] if node else "",
        "phase": node[2] if node else "",
    }


def log_diagnostic(event: str, *, warning: bool = False, **fields: Any) -> None:
    emit = logger.warning if warning else logger.info
    emit("[%s] %s", event, json.dumps({**diagnostic_fields(), **fields}, ensure_ascii=False))


@contextmanager
def model_call_diagnostic(
    *, provider: str, model: str, timeout_seconds: float, input_chars: int
) -> Iterator[dict[str, Any]]:
    from openviking.utils.model_retry import extract_metric_error_code

    fields: dict[str, Any] = {
        **diagnostic_fields(),
        "call_id": uuid4().hex,
        "provider": provider,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "input_chars": input_chars,
    }
    log_diagnostic("VLMCallStarted", **fields)
    started = time.perf_counter()
    status, error_code, error_type = "completed", "OK", ""
    try:
        yield fields
    except BaseException as error:
        status = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
        error_type = type(error).__name__
        error_code = extract_metric_error_code(error)
        # Provider request IDs are structured attributes; never log exception text.
        fields["provider_request_id"] = str(getattr(error, "request_id", None) or "")
        raise
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        log_diagnostic(
            "VLMCallFinished",
            warning=status != "completed" or elapsed >= 30000,
            **fields,
            status=status,
            duration_ms=round(elapsed, 3),
            error_code=error_code,
            error_type=error_type,
        )


@asynccontextmanager
async def model_permit_diagnostic(semaphore: asyncio.Semaphore) -> AsyncIterator[None]:
    log_diagnostic("SemanticVLMPermitWait")
    started = time.perf_counter()
    async with semaphore:
        elapsed = (time.perf_counter() - started) * 1000
        log_diagnostic(
            "SemanticVLMPermitAcquired", warning=elapsed >= 1000, wait_ms=round(elapsed, 3)
        )
        yield
