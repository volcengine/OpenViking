# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for telemetry-wrapped operation execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

from openviking.observability.context import (
    bind_operation_observability_context,
    reset_operation_observability_context,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils import get_logger

from .context import bind_telemetry
from .operation import OperationTelemetry, TelemetrySnapshot
from .request import TelemetryRequest, TelemetrySelection, normalize_telemetry_request
from .span_models import OperationSpanAttributes

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
    snapshot: Optional[TelemetrySnapshot],
    selection: TelemetrySelection,
) -> Optional[dict[str, Any]]:
    """Build a telemetry payload from a finished snapshot."""
    if snapshot is None or not selection.include_payload:
        return None
    return snapshot.to_dict(include_summary=selection.include_summary)


def _log_telemetry_summary(snapshot: Optional[TelemetrySnapshot]) -> None:
    if snapshot is None:
        return
    logger.debug(
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


# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:
    otel_trace = None
    Status = None
    StatusCode = None


def _fill_telemetry_summary_to_span(snapshot, span):
    """fill OperationTelemetry summary into span

    Args:
        snapshot: TelemetrySnapshot object
        span: current OTel span
    """
    if snapshot is None or span is None:
        return

    try:
        # Build OperationSpanAttributes from snapshot for consistent span attributes.
        op_attrs = OperationSpanAttributes.from_telemetry_snapshot(
            operation=snapshot.operation,
            telemetry_id=snapshot.telemetry_id,
            status=snapshot.status,
            summary=snapshot.summary,
        )

        # Apply to span attributes (best-effort).
        span.set_attributes(op_attrs.to_otel_attributes())

        # Attach summary as a span event for debugging (best-effort).
        if snapshot.summary:
            span.add_event("telemetry_summary", attributes={"summary": str(snapshot.summary)})
    except Exception:
        logger.debug("failed to fill telemetry summary into span", exc_info=True)


def _fill_operation_context_from_snapshot(
    operation_attrs: OperationSpanAttributes,
    snapshot: Optional[TelemetrySnapshot],
) -> OperationSpanAttributes:
    """
    Update the bound operation context from a finished telemetry snapshot.

    Returns a new OperationSpanAttributes instance with the updated fields,
    rather than modifying the input object in place. This avoids unintended
    side effects when the same object is referenced elsewhere.

    Args:
        operation_attrs: The original operation attributes.
        snapshot: The telemetry snapshot (optional).

    Returns:
        A new OperationSpanAttributes instance with updated fields, or the original
        instance if snapshot is None.
    """
    if snapshot is None:
        return operation_attrs

    # Create a new instance from the snapshot
    updated = OperationSpanAttributes.from_telemetry_snapshot(
        operation=operation_attrs.operation,
        telemetry_id=snapshot.telemetry_id,
        status=snapshot.summary.get("status"),
        summary=snapshot.summary,
    )

    return updated


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
    operation_attrs = OperationSpanAttributes(
        operation=operation,
        telemetry_id=collector.telemetry_id,
    )
    operation_token = bind_operation_observability_context(operation_attrs)

    # If OTel is available, create an operation span (best-effort).
    span_context_manager = None
    if otel_trace is not None:
        try:
            tracer = otel_trace.get_tracer(__name__)
            span_context_manager = tracer.start_as_current_span(
                name=operation,
                kind=otel_trace.SpanKind.INTERNAL,
            )
        except Exception:
            logger.debug("failed to start operation span", exc_info=True)
            span_context_manager = None

    try:
        try:
            with bind_telemetry(collector):
                if span_context_manager is not None:
                    with span_context_manager as span:
                        try:
                            span.set_attributes(operation_attrs.to_otel_attributes())
                        except Exception:
                            logger.debug(
                                "failed to set operation span attributes (initial)",
                                exc_info=True,
                            )
                        result = await fn()
                else:
                    result = await fn()
        except Exception as exc:
            collector.set_error(operation, type(exc).__name__, str(exc))
            snapshot = collector.finish(status=error_status)
            _log_telemetry_summary(snapshot)

            # If a span exists, record exception and backfill summary (best-effort).
            if otel_trace is not None:
                try:
                    current_span = otel_trace.get_current_span()
                    if current_span is not None and current_span.is_recording():
                        current_span.record_exception(exc)
                        current_span.set_status(Status(StatusCode.ERROR, description=str(exc)))
                        _fill_telemetry_summary_to_span(snapshot, current_span)
                except Exception:
                    logger.debug(
                        "failed to record exception into operation span",
                        exc_info=True,
                    )

            raise

        snapshot = collector.finish(status="ok")
        _log_telemetry_summary(snapshot)

        # If a span exists, backfill summary (best-effort).
        if otel_trace is not None:
            try:
                current_span = otel_trace.get_current_span()
                if current_span is not None and current_span.is_recording():
                    _fill_telemetry_summary_to_span(snapshot, current_span)
            except Exception:
                logger.debug("failed to backfill telemetry summary into span", exc_info=True)

        try:
            if snapshot is not None:
                from openviking.metrics.datasources.telemetry_bridge import (
                    TelemetryBridgeEventDataSource,
                )

                TelemetryBridgeEventDataSource.record_summary(snapshot.summary)
        except Exception:
            logger.debug("failed to record telemetry summary into metrics bridge", exc_info=True)
        telemetry_payload = build_telemetry_payload(
            snapshot,
            selection,
        )
        return TelemetryExecutionResult(
            result=result,
            telemetry=telemetry_payload,
            selection=selection,
        )
    finally:
        reset_operation_observability_context(operation_token)


__all__ = [
    "TelemetryExecutionResult",
    "attach_telemetry_payload",
    "build_telemetry_payload",
    "parse_telemetry_selection",
    "run_with_telemetry",
]
