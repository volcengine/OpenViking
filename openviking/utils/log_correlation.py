# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Stable correlation fields for logs emitted by durable queue work."""

from openviking.service.task_work_index import get_task_context
from openviking.telemetry import get_current_telemetry


def log_correlation(*, task_id: str = "", telemetry_id: str = "", message_id: str = "") -> str:
    """Return searchable task, telemetry, and optional queue-message IDs."""
    task_context = get_task_context()
    task_id = task_id or (task_context.task_id if task_context is not None else "-")
    if not telemetry_id:
        telemetry = get_current_telemetry()
        telemetry_id = telemetry.telemetry_id if telemetry.enabled else "-"
    fields = f"task_id={task_id} telemetry_id={telemetry_id or '-'}"
    return f"{fields} message_id={message_id}" if message_id else fields


__all__ = ["log_correlation"]
