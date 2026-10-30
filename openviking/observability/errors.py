# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Structured error labels shared by models, tasks, and queue deliveries."""

import re


def iter_exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        chain.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return chain


_METRIC_ERROR_CODE_MAX_LENGTH = 64


def normalize_metric_error_code(value: object) -> str | None:
    """Return a bounded structured error code suitable for a metric label."""
    if value is None:
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return str(value)
    if not isinstance(value, str):
        return None
    code = value.strip()
    if not code or len(code) > _METRIC_ERROR_CODE_MAX_LENGTH:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", code):
        return None
    return code


def extract_metric_error_code(error: BaseException) -> str:
    """Extract a low-cardinality structured error code for observability.

    Structured HTTP/SDK error codes are preferred over message parsing.  Free-form
    provider messages and request IDs are intentionally never used as metric labels.
    """
    error_chain = iter_exception_chain(error)
    for exc in error_chain:
        for attr in ("status_code", "error_code", "code"):
            normalized = normalize_metric_error_code(getattr(exc, attr, None))
            if normalized is not None:
                return normalized

        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            candidates = [body.get("status_code"), body.get("error_code"), body.get("code")]
            nested = body.get("error")
            if isinstance(nested, dict):
                candidates.extend(
                    [nested.get("status_code"), nested.get("error_code"), nested.get("code")]
                )
            for value in candidates:
                normalized = normalize_metric_error_code(value)
                if normalized is not None:
                    return normalized

    if any(isinstance(exc, TimeoutError) for exc in error_chain):
        return "timeout"
    if any(isinstance(exc, ConnectionError) for exc in error_chain):
        return "connection_error"
    return "unknown"
