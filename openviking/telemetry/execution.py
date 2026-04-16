# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for telemetry-wrapped operation execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils import get_logger

from .context import bind_telemetry
from .operation import OperationTelemetry, TelemetrySnapshot
from .request import TelemetryRequest, TelemetrySelection, normalize_telemetry_request

T = TypeVar("T")
logger = get_logger(__name__)


@dataclass
class TelemetryExecutionResult(Generic[T]):
    """Executed operation result plus telemetry payloads."""

    result: T
    telemetry: Optional[dict[str, Any]]
    selection: TelemetrySelection


def parse_telemetry_selection(telemetry: TelemetryRequest) -> TelemetrySelection:
    """Validate and normalize a telemetry request for public API usage."""
    try:
        return normalize_telemetry_request(telemetry)
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def build_telemetry_payload(
    snapshot: TelemetrySnapshot | None,
    selection: TelemetrySelection,
) -> dict[str, Any] | None:
    """Build a telemetry payload from a finished snapshot."""
    if snapshot is None or not selection.include_payload:
        return None
    return snapshot.to_dict(include_summary=selection.include_summary)


def _log_telemetry_summary(snapshot: TelemetrySnapshot | None) -> None:
    if snapshot is None:
        return
    logger.info(
        "Telemetry summary (id=%s): %s",
        snapshot.telemetry_id,
        snapshot.summary,
    )


def attach_telemetry_payload(
    result: Any,
    telemetry_payload: Optional[dict[str, Any]],
) -> Any:
    """Attach a telemetry payload to a dict result."""
    if telemetry_payload is None:
        return result

    if result is None:
        payload: dict[str, Any] = {}
        payload["telemetry"] = telemetry_payload
        return payload

    if isinstance(result, dict):
        result["telemetry"] = telemetry_payload
        return result

    return result


async def run_with_telemetry(
    *,
    operation: str,
    telemetry: TelemetryRequest,
    fn: Callable[[], Awaitable[T]],
    error_status: str = "error",
) -> TelemetryExecutionResult[T]:
    """Execute an async operation with a bound operation-scoped collector."""
    selection = parse_telemetry_selection(telemetry)
    collector = OperationTelemetry(
        operation=operation,
        enabled=True,
    )

    try:
        with bind_telemetry(collector):
            result = await fn()
    except Exception as exc:
        collector.set_error(operation, type(exc).__name__, str(exc))
        snapshot = collector.finish(status=error_status)
        _log_telemetry_summary(snapshot)
        raise

    snapshot = collector.finish(status="ok")
    _log_telemetry_summary(snapshot)
    try:
        if snapshot is not None:
            from openviking.metrics.datasources.telemetry_bridge import (
                TelemetryBridgeEventDataSource,
            )

            TelemetryBridgeEventDataSource.record_summary(snapshot.summary)
    except Exception:
        pass
    telemetry_payload = build_telemetry_payload(
        snapshot,
        selection,
    )
    return TelemetryExecutionResult(
        result=result,
        telemetry=telemetry_payload,
        selection=selection,
    )


__all__ = [
    "TelemetryExecutionResult",
    "attach_telemetry_payload",
    "build_telemetry_payload",
    "parse_telemetry_selection",
    "run_with_telemetry",
]
