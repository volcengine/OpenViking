# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Telemetry context helpers."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from .operation import OperationTelemetry

_CURRENT_TELEMETRY: contextvars.ContextVar[OperationTelemetry | None] = contextvars.ContextVar(
    "openviking_operation_telemetry",
    default=None,
)
_CURRENT_TELEMETRY_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openviking_operation_telemetry_stage",
    default=None,
)


def get_current_telemetry() -> OperationTelemetry:
    """Get current operation telemetry or create a request-local disabled collector."""
    telemetry = _CURRENT_TELEMETRY.get()
    if telemetry is None:
        telemetry = OperationTelemetry(operation="noop", enabled=False)
        _CURRENT_TELEMETRY.set(telemetry)
    return telemetry


@contextmanager
def bind_telemetry(handle: OperationTelemetry) -> Iterator[OperationTelemetry]:
    """Bind operation telemetry to current context."""
    token = _CURRENT_TELEMETRY.set(handle)
    try:
        yield handle
    finally:
        _CURRENT_TELEMETRY.reset(token)


def get_current_telemetry_stage() -> str | None:
    """Return the currently bound low-cardinality telemetry stage, if any."""
    stage = _CURRENT_TELEMETRY_STAGE.get()
    if stage is None:
        return None
    normalized = str(stage).strip()
    return normalized or None


@contextmanager
def bind_telemetry_stage(stage: str | None) -> Iterator[str | None]:
    """Bind a fixed stage label to the current context for downstream token attribution."""
    normalized = None if stage is None else str(stage).strip() or None
    token = _CURRENT_TELEMETRY_STAGE.set(normalized)
    try:
        yield normalized
    finally:
        _CURRENT_TELEMETRY_STAGE.reset(token)


__all__ = [
    "bind_telemetry",
    "bind_telemetry_stage",
    "get_current_telemetry",
    "get_current_telemetry_stage",
]
