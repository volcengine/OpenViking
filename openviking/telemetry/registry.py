# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Telemetry registry helpers."""

from __future__ import annotations

import threading

from .operation import OperationTelemetry

_REGISTERED_TELEMETRY: dict[str, OperationTelemetry] = {}
_REGISTERED_TELEMETRY_LOCK = threading.Lock()


def register_telemetry(handle: OperationTelemetry) -> None:
    if not handle.enabled or not handle.telemetry_id:
        return
    with _REGISTERED_TELEMETRY_LOCK:
        _REGISTERED_TELEMETRY[handle.telemetry_id] = handle


def resolve_telemetry(telemetry_id: str) -> OperationTelemetry | None:
    if not telemetry_id:
        return None
    with _REGISTERED_TELEMETRY_LOCK:
        return _REGISTERED_TELEMETRY.get(telemetry_id)


def unregister_telemetry(telemetry_id: str) -> None:
    if not telemetry_id:
        return
    with _REGISTERED_TELEMETRY_LOCK:
        _REGISTERED_TELEMETRY.pop(telemetry_id, None)


__all__ = ["register_telemetry", "resolve_telemetry", "unregister_telemetry"]
