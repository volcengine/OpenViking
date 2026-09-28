# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded-memory task pagination over the durable task store."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
from typing import Any

from openviking_cli.exceptions import InvalidArgumentError

_CURSOR_SECRET = secrets.token_bytes(32)


def page_scope(**filters: Any) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()


def decode_cursor(cursor: str | None, scope: str) -> tuple[float, str] | None:
    if not cursor:
        return None
    try:
        payload, signature = cursor.rsplit(".", 1)
        expected = hmac.new(_CURSOR_SECRET, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        if data["scope"] != scope:
            raise ValueError("scope mismatch")
        timestamp = float(data["time"])
        if not math.isfinite(timestamp):
            raise ValueError("invalid timestamp")
        return timestamp, str(data["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidArgumentError("Invalid task cursor; reload the list") from exc


def encode_cursor(task: dict, scope: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"scope": scope, "time": task["created_at"], "id": task["task_id"]}).encode()
    ).decode()
    return payload + "." + hmac.new(_CURSOR_SECRET, payload.encode(), hashlib.sha256).hexdigest()


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
