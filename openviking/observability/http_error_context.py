# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-local capture of bounded public HTTP error information."""

from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass
from typing import Any

MAX_ERROR_CODE_CHARS = 64
MAX_ERROR_MESSAGE_CHARS = 500
MAX_ERROR_DETAILS_BYTES = 4096

_MAX_DETAIL_STRING_CHARS = 2048
_MAX_DETAIL_KEY_CHARS = 256
_MAX_CONTAINER_ITEMS = 100
_MAX_DETAILS_DEPTH = 8
_TRUNCATED_DETAILS = {"truncated": True}
_REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "password",
        "refresh_token",
        "root_api_key",
        "secret",
        "secret_access_key",
        "secret_key",
        "set_cookie",
        "token",
        "user_key",
    }
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(access[_-]?token|api[_-]?key|authorization|client[_-]?secret|cookie|"
    r"credential|password|refresh[_-]?token|root[_-]?api[_-]?key|secret|"
    r"secret[_-]?access[_-]?key|secret[_-]?key|set[_-]?cookie|token|user[_-]?key)"
    r"(\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,}\]]+)"
)
_TOKEN_RE = re.compile(
    r"(?i)\b(?P<scheme>Bearer|Basic)\s+[a-zA-Z0-9._~+/=-]+|"
    r"\b(?:sk-|cr_|ghp_|ntn_|xox[baprs]-)[a-zA-Z0-9._-]+"
)


@dataclass(frozen=True, slots=True)
class CapturedHTTPError:
    """Sanitized public error fields attached to one completed HTTP request."""

    code: str
    message: str
    details: dict[str, Any] | None = None


@dataclass(slots=True)
class _HTTPErrorContext:
    error: CapturedHTTPError | None = None


_HTTP_ERROR_CONTEXT: contextvars.ContextVar[_HTTPErrorContext | None] = contextvars.ContextVar(
    "openviking_http_error_context",
    default=None,
)


def bind_http_error_context() -> contextvars.Token:
    """Install a mutable request-local holder and return its reset token."""
    return _HTTP_ERROR_CONTEXT.set(_HTTPErrorContext())


def reset_http_error_context(token: contextvars.Token) -> None:
    """Restore the previous request-local error holder."""
    _HTTP_ERROR_CONTEXT.reset(token)


def get_captured_http_error() -> CapturedHTTPError | None:
    """Return the public error captured for the current request, if any."""
    state = _HTTP_ERROR_CONTEXT.get()
    return state.error if state is not None else None


def capture_public_http_error(
    *,
    code: Any,
    message: Any,
    details: Any = None,
) -> None:
    """Capture the standard error envelope without changing the HTTP response."""
    state = _HTTP_ERROR_CONTEXT.get()
    if state is None:
        return
    try:
        state.error = sanitize_public_http_error(code=code, message=message, details=details)
    except Exception:
        # Audit capture is a side channel and must never change the API response.
        return


def sanitize_public_http_error(
    *,
    code: Any,
    message: Any,
    details: Any = None,
) -> CapturedHTTPError:
    """Return bounded, credential-redacted fields safe for durable audit storage."""
    return CapturedHTTPError(
        code=_bounded_text(code, MAX_ERROR_CODE_CHARS),
        message=_bounded_text(message, MAX_ERROR_MESSAGE_CHARS),
        details=sanitize_public_error_details(details),
    )


def sanitize_public_error_details(details: Any) -> dict[str, Any] | None:
    """Return a bounded JSON object, or ``None`` when no object was supplied."""
    if not isinstance(details, dict):
        return None
    sanitized = _sanitize_detail_value(details, depth=0)
    if not isinstance(sanitized, dict):
        return None
    try:
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return dict(_TRUNCATED_DETAILS)
    if len(encoded) > MAX_ERROR_DETAILS_BYTES:
        return dict(_TRUNCATED_DETAILS)
    return sanitized


def serialize_public_error_details(details: Any) -> str | None:
    """Serialize bounded public details for the SQLite TEXT column."""
    sanitized = sanitize_public_error_details(details)
    if sanitized is None:
        return None
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _bounded_text(value: Any, max_chars: int) -> str:
    text = _redact_sensitive_text(str(value or ""))
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    return text[: max(max_chars - len(suffix), 0)] + suffix[:max_chars]


def _redact_sensitive_text(value: str) -> str:
    def _redact_token(match: re.Match[str]) -> str:
        scheme = match.groupdict().get("scheme")
        return f"{scheme} {_REDACTED}" if scheme else _REDACTED

    value = _TOKEN_RE.sub(_redact_token, value)

    def _redact_assignment(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{_REDACTED}"

    return _SENSITIVE_ASSIGNMENT_RE.sub(_redact_assignment, value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_access_token", "_refresh_token", "_client_secret", "_password")
    )


def _sanitize_detail_value(value: Any, *, depth: int) -> Any:
    if depth >= _MAX_DETAILS_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTAINER_ITEMS:
                break
            raw_key = str(key)
            key_text = _bounded_text(raw_key, _MAX_DETAIL_KEY_CHARS)
            result[key_text] = (
                _REDACTED
                if _is_sensitive_key(raw_key)
                else _sanitize_detail_value(item, depth=depth + 1)
            )
        if len(value) > _MAX_CONTAINER_ITEMS:
            result["truncated"] = True
        return result
    if isinstance(value, (list, tuple)):
        items = [
            _sanitize_detail_value(item, depth=depth + 1) for item in value[:_MAX_CONTAINER_ITEMS]
        ]
        if len(value) > _MAX_CONTAINER_ITEMS:
            items.append("[truncated]")
        return items
    if isinstance(value, str):
        return _bounded_text(value, _MAX_DETAIL_STRING_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_text(value, _MAX_DETAIL_STRING_CHARS)
