# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import ast
import re
from typing import Any, Iterator

from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSConnectionError,
    AGFSHTTPError,
    AGFSNotFoundError,
    AGFSTimeoutError,
)
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    OpenVikingError,
    PermissionDeniedError,
    UnavailableError,
)

_UPSTREAM_HTTP_STATUS_TO_ERROR_CODE = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    402: "RESOURCE_EXHAUSTED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    408: "DEADLINE_EXCEEDED",
    409: "CONFLICT",
    422: "INVALID_ARGUMENT",
    429: "RESOURCE_EXHAUSTED",
    500: "UNAVAILABLE",
    502: "UNAVAILABLE",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}

_KNOWN_HTTP_STATUS_CODES = frozenset(_UPSTREAM_HTTP_STATUS_TO_ERROR_CODE)
_UPSTREAM_ERROR_MARKERS = (
    "api error",
    "apierror",
    "badrequesterror",
    "authenticationerror",
    "permissiondeniederror",
    "ratelimiterror",
    "httpstatuserror",
    "openai",
    "litellm",
    "volcengine",
    "ark",
    "gemini",
    "jina",
    "voyage",
    "cohere",
    "dashscope",
    "minimax",
    "embedding",
    "embedder",
    "vlm",
    "model",
    "upstream",
    "invalid api key",
    "unauthorized",
    "forbidden",
    "too many requests",
    "rate limit",
    "quota",
    "accountoverdue",
)
_API_KEY_CONFIG_MARKERS = (
    "vlm configuration",
    "embedding",
    "embedder",
    "provider",
    "openai",
    "azure",
    "volcengine",
    "jina",
    "gemini",
    "voyage",
    "dashscope",
    "minimax",
    "cohere",
)
_HTTP_STATUS_PATTERNS = (
    re.compile(r"\bHTTP\s*(\d{3})\b", re.IGNORECASE),
    re.compile(r"\bstatus(?:\s+code)?\s*[:=]?\s*(\d{3})\b", re.IGNORECASE),
    re.compile(r"\berror\s+code\s*[:=]?\s*(\d{3})\b", re.IGNORECASE),
)


def _iter_exception_chain(exc: Exception) -> Iterator[BaseException]:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop(0)
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        yield current
        for nested in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(nested, BaseException) and id(nested) not in seen:
                pending.append(nested)


def _coerce_http_status(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    if 100 <= status <= 599:
        return status
    return None


def _normalize_message(message: str) -> str:
    return " ".join(str(message).split())


def _dedupe_messages(messages: list[str]) -> list[str]:
    result: list[str] = []
    for message in messages:
        normalized = _normalize_message(message)
        if not normalized:
            continue
        if any(normalized == existing or normalized in existing for existing in result):
            continue
        result = [existing for existing in result if existing not in normalized]
        result.append(normalized)
    return result


def _exception_chain_text(exc: Exception) -> str:
    return "\n".join(_dedupe_messages([str(item) for item in _iter_exception_chain(exc)]))


def _trim_message(message: str, limit: int = 500) -> str:
    normalized = _normalize_message(message)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _iter_braced_segments(text: str) -> Iterator[str]:
    start: int | None = None
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : index + 1]
                start = None


def _extract_payload_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _extract_provider_error_message(text: str) -> str | None:
    for segment in _iter_braced_segments(text):
        try:
            payload = ast.literal_eval(segment)
        except (SyntaxError, ValueError):
            continue
        message = _extract_payload_message(payload)
        if message:
            return message
    return None


def _upstream_detail_message(exc: Exception) -> str:
    text = _exception_chain_text(exc)
    return _extract_provider_error_message(text) or text


def _looks_like_upstream_model_error(exc: Exception) -> bool:
    text = _exception_chain_text(exc).lower()
    return any(marker in text for marker in _UPSTREAM_ERROR_MARKERS)


def _is_model_api_key_configuration_error(exc: Exception) -> bool:
    text = _exception_chain_text(exc).lower()
    if "api_key" not in text and "api key" not in text:
        return False
    if "invalid api key" in text or "unauthorized" in text:
        return False
    missing_key_phrases = (
        "requires 'api_key'",
        "requires api_key",
        "api_key is required",
        "api key is required",
        "requires 'api key'",
    )
    if not any(phrase in text for phrase in missing_key_phrases):
        return False
    if any(marker in text for marker in _API_KEY_CONFIG_MARKERS):
        return True
    return text.strip() in (
        "api_key is required",
        "api key is required",
    )


def _extract_structured_http_status(
    exc: Exception,
) -> tuple[int, BaseException] | tuple[None, None]:
    for item in _iter_exception_chain(exc):
        for attr in ("status_code", "http_status", "status"):
            status = _coerce_http_status(getattr(item, attr, None))
            if status is not None:
                return status, item
        code = getattr(item, "code", None)
        if not isinstance(code, str):
            status = _coerce_http_status(code)
            if status is not None:
                return status, item
        response = getattr(item, "response", None)
        if response is not None:
            for attr in ("status_code", "status"):
                status = _coerce_http_status(getattr(response, attr, None))
                if status is not None:
                    return status, item
    return None, None


def _extract_text_http_status(exc: Exception) -> int | None:
    if not _looks_like_upstream_model_error(exc):
        return None
    text = _exception_chain_text(exc)
    for pattern in _HTTP_STATUS_PATTERNS:
        match = pattern.search(text)
        if match:
            status = _coerce_http_status(match.group(1))
            if status is not None:
                return status
    for status in _KNOWN_HTTP_STATUS_CODES:
        if re.search(rf"\b{status}\b", text):
            return status
    return None


def _upstream_code_for_status(status: int) -> str:
    if status in _UPSTREAM_HTTP_STATUS_TO_ERROR_CODE:
        return _UPSTREAM_HTTP_STATUS_TO_ERROR_CODE[status]
    if 400 <= status < 500:
        return "INVALID_ARGUMENT"
    if 500 <= status < 600:
        return "UNAVAILABLE"
    return "UNKNOWN"


def _build_upstream_error(
    *,
    code: str,
    message: str,
    status: int | None = None,
    source: BaseException | None = None,
) -> OpenVikingError:
    labels = {
        "INVALID_ARGUMENT": "Upstream model request was rejected",
        "UNAUTHENTICATED": "Upstream model authentication failed",
        "PERMISSION_DENIED": "Upstream model permission denied",
        "NOT_FOUND": "Upstream model resource not found",
        "CONFLICT": "Upstream model request conflicted",
        "RESOURCE_EXHAUSTED": "Upstream model quota or rate limit exceeded",
        "DEADLINE_EXCEEDED": "Upstream model request timed out",
        "UNAVAILABLE": "Upstream model service unavailable",
    }
    detail_message = _trim_message(message)
    display = labels.get(code, "Upstream model error")
    if status is not None:
        display = f"{display} (HTTP {status})"
    if detail_message:
        display = f"{display}: {detail_message}"
    details: dict[str, Any] = {"upstream_message": detail_message}
    if status is not None:
        details["upstream_status_code"] = status
    if source is not None:
        details["upstream_error_type"] = type(source).__name__
    return OpenVikingError(display, code=code, details=details)


def _map_upstream_api_error(exc: Exception) -> OpenVikingError | None:
    if _is_model_api_key_configuration_error(exc):
        return FailedPreconditionError(
            "Model provider API key is not configured",
            details={"reason": _trim_message(_exception_chain_text(exc))},
        )

    status, source = _extract_structured_http_status(exc)
    if status is None:
        status = _extract_text_http_status(exc)
    if status is not None:
        code = _upstream_code_for_status(status)
        if code == "UNKNOWN":
            return None
        return _build_upstream_error(
            code=code,
            status=status,
            source=source,
            message=_upstream_detail_message(exc),
        )

    if not _looks_like_upstream_model_error(exc):
        return None
    text = _exception_chain_text(exc)
    lowered = text.lower()
    if "invalid api key" in lowered or "unauthorized" in lowered:
        return _build_upstream_error(
            code="UNAUTHENTICATED", message=_upstream_detail_message(exc), source=exc
        )
    if "forbidden" in lowered:
        return _build_upstream_error(
            code="PERMISSION_DENIED", message=_upstream_detail_message(exc), source=exc
        )
    if any(
        marker in lowered
        for marker in (
            "too many requests",
            "rate limit",
            "ratelimit",
            "quota",
            "accountoverdue",
            "resource exhausted",
        )
    ):
        return _build_upstream_error(
            code="RESOURCE_EXHAUSTED", message=_upstream_detail_message(exc), source=exc
        )
    return None


def is_not_found_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, AGFSNotFoundError):
        return True
    if isinstance(exc, AGFSHTTPError) and exc.status_code == 404:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "not found",
            "no such file",
            "does not exist",
        )
    )


def is_invalid_uri_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "invalid uri",
            "invalid viking uri",
            "invalid viking://",
        )
    )


def _not_found_error(resource: str | None, resource_type: str) -> NotFoundError:
    return NotFoundError(resource or "", resource_type)


def map_exception(
    exc: Exception,
    *,
    resource: str | None = None,
    resource_type: str = "resource",
) -> OpenVikingError | None:
    if isinstance(exc, OpenVikingError):
        return exc
    if isinstance(exc, ResourceBusyError):
        details: dict[str, Any] = {
            "resource": exc.uri or resource,
            "uri": exc.uri or resource,
            "conflict_type": exc.conflict_type,
            "retryable": exc.retryable,
        }
        return OpenVikingError(str(exc), code="CONFLICT", details=details)
    if isinstance(exc, LockAcquisitionError):
        details = {
            "resource": resource,
            "uri": resource,
            "conflict_type": "path_busy",
            "retryable": True,
        }
        return OpenVikingError(str(exc), code="CONFLICT", details=details)
    if isinstance(exc, PermissionError):
        return PermissionDeniedError(str(exc), resource=resource)
    if isinstance(exc, FileNotFoundError):
        return _not_found_error(resource, resource_type)
    if _is_model_api_key_configuration_error(exc):
        return FailedPreconditionError(
            "Model provider API key is not configured",
            details={"reason": _trim_message(_exception_chain_text(exc))},
        )
    if isinstance(exc, ValueError):
        message = str(exc)
        if is_invalid_uri_error(exc):
            return InvalidURIError(resource or message, message)
        if "not a directory" in message.lower():
            details = {"resource": resource} if resource else None
            return FailedPreconditionError(message, details=details)
        return InvalidArgumentError(message, details={"resource": resource} if resource else None)
    if isinstance(exc, (AGFSConnectionError, AGFSTimeoutError)):
        return UnavailableError("storage backend", reason=str(exc))
    if isinstance(exc, AGFSHTTPError):
        if exc.status_code == 404 or is_not_found_error(exc):
            return _not_found_error(resource, resource_type)
        if exc.status_code == 403:
            return PermissionDeniedError(str(exc), resource=resource)
        if exc.status_code == 409:
            return ConflictError(str(exc), resource=resource)
        if exc.status_code == 400:
            return InvalidArgumentError(
                str(exc), details={"resource": resource} if resource else None
            )
        if exc.status_code in {500, 502, 503, 504}:
            return UnavailableError("storage backend", reason=str(exc))
    if isinstance(exc, AGFSClientError):
        message = str(exc)
        if is_not_found_error(exc):
            return _not_found_error(resource, resource_type)
        if is_invalid_uri_error(exc):
            return InvalidURIError(resource or message, message)
        lowered = message.lower()
        if "not a directory" in lowered:
            details = {"resource": resource} if resource else None
            return FailedPreconditionError(message, details=details)
        if "permission denied" in lowered:
            return PermissionDeniedError(message, resource=resource)
        if "already exists" in lowered:
            return ConflictError(message, resource=resource)
        if "timeout" in lowered or "connection refused" in lowered:
            return UnavailableError("storage backend", reason=message)
    upstream_mapped = _map_upstream_api_error(exc)
    if upstream_mapped is not None:
        return upstream_mapped
    message = str(exc)
    lowered = message.lower()
    if is_not_found_error(exc):
        return _not_found_error(resource, resource_type)
    if is_invalid_uri_error(exc):
        return InvalidURIError(resource or message, message)
    if "permission denied" in lowered or "access denied" in lowered:
        return PermissionDeniedError(message, resource=resource)
    if "timeout" in lowered or "connection refused" in lowered:
        return UnavailableError("storage backend", reason=message)
    return None
