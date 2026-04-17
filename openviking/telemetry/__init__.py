# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking telemetry runtime and operation telemetry helpers."""

from . import tracer as tracer_module
from .context import (
    bind_telemetry,
    bind_telemetry_stage,
    get_current_telemetry,
    get_current_telemetry_stage,
)
from .operation import OperationTelemetry, TelemetrySnapshot
from .registry import register_telemetry, resolve_telemetry, unregister_telemetry
from .request import TelemetryRequest, TelemetrySelection, normalize_telemetry_request
from .runtime import get_telemetry_runtime, set_telemetry_runtime
from .tracer import tracer

__all__ = [
    "OperationTelemetry",
    "TelemetryRequest",
    "TelemetrySelection",
    "TelemetrySnapshot",
    "bind_telemetry",
    "bind_telemetry_stage",
    "get_current_telemetry",
    "get_current_telemetry_stage",
    "get_telemetry_runtime",
    "normalize_telemetry_request",
    "register_telemetry",
    "resolve_telemetry",
    "set_telemetry_runtime",
    "tracer",
    "tracer_module",
    "unregister_telemetry",
]
