# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded-memory task pagination over the durable task store."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from typing import Any

from openviking_cli.exceptions import InvalidArgumentError


def page_scope(**filters: Any) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()


def decode_cursor(cursor: str | None, scope: str) -> tuple[float, str] | None:
    """Decode a keyset position issued for the same caller and filters.

    Cursors are positions, not authorization tokens: the task store applies the
    caller's account, user and filter on every page, so editing a position can
    only skip rows that caller can already see. They therefore carry no
    node-local signing state, and a cursor issued by one server process remains
    valid when the next page is served by another worker or after a restart
    (the same contract as ``openviking.resource.watch_pagination``).
    """
    if not cursor:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError("cursor too long")
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if data["scope"] != scope:
            raise ValueError("scope mismatch")
        timestamp = data["time"]
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            raise ValueError("invalid timestamp")
        task_id = data["id"]
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 255:
            raise ValueError("invalid task id")
        return float(timestamp), task_id
    except (ValueError, KeyError, TypeError, OverflowError, UnicodeError) as exc:
        raise InvalidArgumentError("Invalid task cursor; reload the list") from exc


def encode_cursor(task: dict, scope: str) -> str:
    payload = json.dumps(
        {"scope": scope, "time": task["created_at"], "id": task["task_id"]},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode()


def matches(
    task: dict,
    *,
    task_type: str | None,
    status: str | None,
    resource_id: str | None,
    include_internal: bool,
    q: str | None,
) -> bool:
    if task_type and task.get("task_type") != task_type:
        return False
    if status and task.get("status") != status:
        return False
    if resource_id and task.get("resource_id") != resource_id:
        return False
    meta = task.get("meta") or {}
    if not include_internal and meta.get("internal"):
        return False
    request = meta.get("request") or {}
    fields = [
        task.get("task_id", ""),
        request.get("skill", ""),
        request.get("to", ""),
        *(request.get("from") or []),
    ]
    return not q or q.casefold() in " ".join(str(v) for v in fields).casefold()
