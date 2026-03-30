# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory telemetry backend."""

from openviking.telemetry.operation import OperationTelemetry


class MemoryOperationTelemetry(OperationTelemetry):
    """In-process operation telemetry collector."""


__all__ = ["MemoryOperationTelemetry"]
