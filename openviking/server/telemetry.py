# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""HTTP router helpers for operation telemetry."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from openviking.telemetry import TelemetryRequest, TelemetrySelection
from openviking.telemetry.execution import (
    TelemetryExecutionResult,
    parse_telemetry_selection,
    run_with_telemetry,
)


def resolve_selection(telemetry: TelemetryRequest) -> TelemetrySelection:
    """Validate a router telemetry request without starting execution."""
    return parse_telemetry_selection(telemetry)


async def run_operation(
    *,
    operation: str,
    telemetry: TelemetryRequest,
    fn: Callable[[], Awaitable[Any]],
) -> TelemetryExecutionResult[Any]:
    """Execute a router operation with request-scoped telemetry."""
    return await run_with_telemetry(
        operation=operation,
        telemetry=telemetry,
        fn=fn,
    )
