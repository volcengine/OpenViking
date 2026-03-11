# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Telemetry context helpers."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from .operation import OperationTelemetry

_NOOP_TELEMETRY = OperationTelemetry(operation="noop", enabled=False)
_CURRENT_TELEMETRY: contextvars.ContextVar[OperationTelemetry] = contextvars.ContextVar(
    "openviking_operation_telemetry",
    default=_NOOP_TELEMETRY,
)


def get_current_telemetry() -> OperationTelemetry:
    """Get current operation telemetry or disabled no-op collector."""
    return _CURRENT_TELEMETRY.get()


@contextmanager
def bind_telemetry(handle: OperationTelemetry) -> Iterator[OperationTelemetry]:
    """Bind operation telemetry to current context."""
    token = _CURRENT_TELEMETRY.set(handle)
    try:
        yield handle
    finally:
        _CURRENT_TELEMETRY.reset(token)


__all__ = ["bind_telemetry", "get_current_telemetry"]
