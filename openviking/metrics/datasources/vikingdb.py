# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Event datasource for bounded VectorDB operation metrics."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from .base import EventMetricDataSource

_operation_depth: ContextVar[int] = ContextVar("vikingdb_metrics_operation_depth", default=0)
_suppressed: ContextVar[bool] = ContextVar("vikingdb_metrics_suppressed", default=False)
_handled_error: ContextVar[Exception | None] = ContextVar(
    "vikingdb_metrics_handled_error", default=None
)
_FALSE_RESULT_FAILURE_OPERATIONS = frozenset(
    {"create_collection", "drop_collection", "update_collection_description", "health_check"}
)


def _exception_fields(error: Exception) -> tuple[str, str, str, str]:
    """Return bounded `(source, code, category, retryable)` fields for an exception."""
    code = getattr(error, "error_code", None) or getattr(error, "code", None)
    source = getattr(error, "error_source", None)
    category = getattr(error, "error_category", None)
    retryable = bool(getattr(error, "retryable", False))
    if code is not None:
        return str(source or "backend"), str(code), str(category or "unknown"), str(retryable).lower()
    if isinstance(error, PermissionError):
        return "client", "PERMISSION_DENIED", "permission_denied", "false"
    if isinstance(error, ValueError):
        return "client", "INVALID_ARGUMENT", "invalid_argument", "false"
    if isinstance(error, TimeoutError):
        return "client", "TIMEOUT", "timeout", "true"
    return "core", type(error).__name__.upper(), "internal", "false"


class VikingDBEventDataSource(EventMetricDataSource):
    """Publish operation and retry events without coupling VectorDB code to a registry."""

    @classmethod
    def enter_operation(cls) -> tuple[bool, object, object]:
        depth = _operation_depth.get()
        depth_token = _operation_depth.set(depth + 1)
        error_token = _handled_error.set(None)
        return depth == 0 and not _suppressed.get(), depth_token, error_token

    @classmethod
    def exit_operation(cls, depth_token: object, error_token: object) -> None:
        _handled_error.reset(error_token)
        _operation_depth.reset(depth_token)

    @classmethod
    def record_handled_error(cls, error: Exception) -> None:
        """Preserve an exception when a legacy API converts it into a return value."""
        _handled_error.set(error)

    @classmethod
    def handled_error(cls) -> Exception | None:
        """Return the exception captured by the current outermost backend operation."""
        return _handled_error.get()

    @classmethod
    @contextmanager
    def suppress_operations(cls) -> Iterator[None]:
        """Suppress business-operation metrics for probes and metrics self-collection."""
        token = _suppressed.set(True)
        try:
            yield
        finally:
            _suppressed.reset(token)

    @classmethod
    def record_operation(
        cls, *, backend: str, access_mode: str, operation: str, duration_seconds: float,
        result: Any = None, error: Exception | None = None,
    ) -> None:
        """Emit one outermost VectorDB operation outcome with bounded labels."""
        outcome = "success"
        error_source, error_code, error_category, retryable = "none", "OK", "none", "false"
        if error is not None:
            outcome = "error"
            error_source, error_code, error_category, retryable = _exception_fields(error)
        elif getattr(result, "ok", True) is False:
            outcome = "error"
            error_source, error_code, error_category = "backend", str(getattr(result, "error_code", None) or "UNKNOWN"), "backend_error"
        elif result is False and operation in _FALSE_RESULT_FAILURE_OPERATIONS:
            outcome = "error"
            error_source, error_code, error_category = "backend", "FALSE_RESULT", "backend_error"
        elif operation == "upsert" and not result:
            outcome = "error"
            error_source, error_code, error_category = "backend", "EMPTY_RESULT", "backend_error"
        payload = {
            "backend": str(backend or "unknown"), "access_mode": str(access_mode or "default"),
            "operation": str(operation), "outcome": outcome, "error_source": error_source,
            "error_code": error_code, "error_category": error_category, "retryable": retryable,
            "duration_seconds": max(0.0, float(duration_seconds)),
        }
        if outcome == "success":
            if operation in {"upsert_many", "delete", "strict_delete"} and isinstance(result, (list, tuple, set, int)):
                payload["records"] = len(result) if not isinstance(result, int) else result
                payload["record_direction"] = "written" if operation == "upsert_many" else "deleted"
            elif operation == "upsert" and result:
                payload["records"], payload["record_direction"] = 1, "written"
            elif operation in {"query", "strict_query", "search", "search_by_random", "search_by_keywords"} and isinstance(result, (list, tuple)):
                payload["search_results"] = len(result)
                payload["search_type"] = operation.removeprefix("search_") or "vector"
        cls._emit("vikingdb.operation", payload)

    @classmethod
    def record_retry(
        cls,
        *,
        backend: str,
        operation: str,
        delay_seconds: float,
        error: Exception | None = None,
        error_source: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Emit one extra retry attempt; the initial request is intentionally excluded."""
        if error is not None:
            error_source, error_code, _, _ = _exception_fields(error)
        cls._emit("vikingdb.retry", {
            "backend": str(backend or "unknown"), "operation": str(operation),
            "error_source": str(error_source or "unknown"), "error_code": str(error_code or "UNKNOWN"),
            "delay_seconds": max(0.0, float(delay_seconds)),
        })


__all__ = ["VikingDBEventDataSource"]
