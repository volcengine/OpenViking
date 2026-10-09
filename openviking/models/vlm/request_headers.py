# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve request-scoped placeholders in configured VLM headers."""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from openviking.observability.context import get_root_observability_context
from openviking.service.task_work_index import get_task_context

REQUEST_ID_PLACEHOLDER = "{request_id}"

_VLM_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openviking_vlm_request_id",
    default=None,
)


def resolve_vlm_request_id(request_id: str | None = None) -> str:
    """Return the explicit, bound, task, HTTP, or generated VLM request identity."""
    explicit = str(request_id or "").strip()
    if explicit:
        return explicit

    bound = _VLM_REQUEST_ID.get()
    if bound:
        return bound

    task = get_task_context()
    task_id = str(task.task_id if task is not None else "").strip()
    if task_id:
        return task_id

    root = get_root_observability_context()
    root_request_id = str(root.request_id if root is not None else "").strip()
    if root_request_id:
        return root_request_id

    return f"vlm-{uuid.uuid4().hex}"


@contextmanager
def bind_vlm_request_id(request_id: str | None = None) -> Iterator[str]:
    """Bind one request identity for nested VLM calls and retries."""
    resolved = resolve_vlm_request_id(request_id)
    token = _VLM_REQUEST_ID.set(resolved)
    try:
        yield resolved
    finally:
        _VLM_REQUEST_ID.reset(token)


def static_extra_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Return headers that do not require request-time expansion."""
    return {
        key: value
        for key, value in (headers or {}).items()
        if not isinstance(value, str) or REQUEST_ID_PLACEHOLDER not in value
    }


def dynamic_extra_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Resolve only headers that contain the request-id placeholder."""
    dynamic = {
        key: value
        for key, value in (headers or {}).items()
        if isinstance(value, str) and REQUEST_ID_PLACEHOLDER in value
    }
    if not dynamic:
        return {}

    request_id = resolve_vlm_request_id()
    return {
        key: value.replace(REQUEST_ID_PLACEHOLDER, request_id) for key, value in dynamic.items()
    }


def resolve_extra_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Return static headers plus request-time placeholder expansion."""
    return {**static_extra_headers(headers), **dynamic_extra_headers(headers)}


__all__ = [
    "REQUEST_ID_PLACEHOLDER",
    "bind_vlm_request_id",
    "dynamic_extra_headers",
    "resolve_extra_headers",
    "resolve_vlm_request_id",
    "static_extra_headers",
]
