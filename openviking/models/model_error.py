# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Provider-neutral model API errors and user-facing error briefs."""

from __future__ import annotations

import functools
import inspect
import re
from enum import Enum, IntEnum
from typing import Any, Callable, TypeVar, cast


class ModelErrorKind(str, Enum):
    TRANSIENT_SERVICE_ERROR = "transient_service_error"
    TRANSIENT_NETWORK_ERROR = "transient_network_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    AUTHENTICATION_ERROR = "authentication_error"
    PERMISSION_DENIED = "permission_denied"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MODEL_NOT_FOUND = "model_not_found"
    UNSUPPORTED = "unsupported"
    INVALID_REQUEST = "invalid_request"
    INPUT_TOO_LARGE = "input_too_large"
    CONTENT_SAFETY = "content_safety"
    UNKNOWN = "unknown"


class ModelError(Exception):
    """A structured model API failure reported by a provider boundary."""

    def __init__(
        self,
        kind: ModelErrorKind,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.status_code = status_code
        self.code = code


_CODE_KINDS = {
    # Authentication and authorization.
    "APIKEYINVALID": ModelErrorKind.AUTHENTICATION_ERROR,
    "INVALIDAPIKEY": ModelErrorKind.AUTHENTICATION_ERROR,
    "UNAUTHENTICATED": ModelErrorKind.AUTHENTICATION_ERROR,
    "AUTHENTICATIONERROR": ModelErrorKind.AUTHENTICATION_ERROR,
    "PERMISSIONDENIED": ModelErrorKind.PERMISSION_DENIED,
    "ACCESSDENIED": ModelErrorKind.PERMISSION_DENIED,
    # Quota and billing exhaustion.
    "INSUFFICIENTQUOTA": ModelErrorKind.QUOTA_EXHAUSTED,
    "ACCOUNTQUOTAEXCEEDED": ModelErrorKind.QUOTA_EXHAUSTED,
    "MODELQUOTAEXCEEDED": ModelErrorKind.QUOTA_EXHAUSTED,
    "QUOTAEXCEEDED": ModelErrorKind.QUOTA_EXHAUSTED,
    "BILLINGDISABLED": ModelErrorKind.QUOTA_EXHAUSTED,
    "BILLINGHARDLIMITREACHED": ModelErrorKind.QUOTA_EXHAUSTED,
    "ACCOUNTOVERDUE": ModelErrorKind.QUOTA_EXHAUSTED,
    # Request rate limits.
    "RATELIMITEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
    "SERVINGCAPACITYEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
    "REQUESTLIMITEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
    "REQUESTBURSTTOOFAST": ModelErrorKind.RATE_LIMIT_ERROR,
    "TOOMANYREQUESTS": ModelErrorKind.RATE_LIMIT_ERROR,
    "MODELACCOUNTRPMRATELIMITEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
    "MODELACCOUNTTPMRATELIMITEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
    "MODELACCOUNTTOTALTOKENRATELIMITEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
    # Request shape and model capabilities.
    "CONTEXTLENGTHEXCEEDED": ModelErrorKind.INPUT_TOO_LARGE,
    "INPUTTEXTEXCEEDSLIMIT": ModelErrorKind.INPUT_TOO_LARGE,
    "CONTENTTOOLARGE": ModelErrorKind.INPUT_TOO_LARGE,
    "CONTENTFILTER": ModelErrorKind.CONTENT_SAFETY,
    "CONTENTPOLICYVIOLATION": ModelErrorKind.CONTENT_SAFETY,
    "SAFETY": ModelErrorKind.CONTENT_SAFETY,
    "MODELNOTFOUND": ModelErrorKind.MODEL_NOT_FOUND,
    "INVALIDMODEL": ModelErrorKind.MODEL_NOT_FOUND,
    "INVALIDENDPOINTORMODELNOTFOUND": ModelErrorKind.MODEL_NOT_FOUND,
    "UNSUPPORTEDPARAMETER": ModelErrorKind.UNSUPPORTED,
    "UNSUPPORTEDVALUE": ModelErrorKind.UNSUPPORTED,
}

# MiniMax returns endpoint-specific business codes in an HTTP 200 response.
# Keep numeric codes provider-scoped: the same number has no portable meaning
# across model APIs. These are the codes documented for the embedding endpoint.
_PROVIDER_CODE_KINDS = {
    "minimax": {
        "1000": ModelErrorKind.TRANSIENT_SERVICE_ERROR,
        "1001": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
        "1002": ModelErrorKind.RATE_LIMIT_ERROR,
        "1004": ModelErrorKind.AUTHENTICATION_ERROR,
        "1008": ModelErrorKind.QUOTA_EXHAUSTED,
        "1013": ModelErrorKind.TRANSIENT_SERVICE_ERROR,
        "1026": ModelErrorKind.CONTENT_SAFETY,
        "1027": ModelErrorKind.CONTENT_SAFETY,
        "2013": ModelErrorKind.INVALID_REQUEST,
        "2049": ModelErrorKind.AUTHENTICATION_ERROR,
    }
}

_TRANSIENT_SERVICE_STATUS = {409, 500, 502, 503}
_TRANSIENT_NETWORK_STATUS = {408, 504}

_METRIC_ERROR_CODE_MAX_LENGTH = 64


class _EvidenceRank(IntEnum):
    FALLBACK = 0
    HTTP_STATUS = 1
    SDK_CLASS = 2
    PROVIDER_CODE = 3
    EXPLICIT_KIND = 4


def model_exception_chain(error: BaseException) -> list[BaseException]:
    """Return every reachable cause/context exception once, outermost first."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        chain.append(current)
        seen.add(id(current))
        for nested in (current.__cause__, current.__context__):
            if nested is not None and id(nested) not in seen:
                pending.append(nested)
    return chain


def _normalized_code(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str) or not value:
        return None
    return "".join(character for character in value.upper() if character.isalnum())


def _kind_from_code_value(
    value: object,
    provider: str | None = None,
) -> ModelErrorKind | None:
    normalized = _normalized_code(value)
    if normalized is None:
        return None
    if provider:
        kind = _PROVIDER_CODE_KINDS.get(provider.lower(), {}).get(normalized)
        if kind is not None:
            return kind
    return _CODE_KINDS.get(normalized)


def _response_body(error: BaseException) -> dict[str, Any]:
    value = getattr(error, "body", None)
    if not isinstance(value, dict):
        response = getattr(error, "response", None)
        try:
            value = response.json() if response is not None else None
        except Exception:
            value = None
    return value if isinstance(value, dict) else {}


def _body(error: BaseException) -> dict[str, Any]:
    value = _response_body(error)
    nested = value.get("error")
    if isinstance(nested, dict):
        return {**value, **nested}
    return value


def _normalize_metric_error_code(value: object) -> str | None:
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


def extract_model_error_code(error: BaseException) -> str:
    """Extract a bounded structured code without parsing free-form messages."""
    error_chain = model_exception_chain(error)
    for item in error_chain:
        for attr in ("status_code", "error_code", "code"):
            normalized = _normalize_metric_error_code(getattr(item, attr, None))
            if normalized is not None:
                return normalized

        response = getattr(item, "response", None)
        normalized = _normalize_metric_error_code(getattr(response, "status_code", None))
        if normalized is not None:
            return normalized

        body = _response_body(item)
        candidates = [body.get("status_code"), body.get("error_code"), body.get("code")]
        nested = body.get("error")
        if isinstance(nested, dict):
            candidates.extend(
                [nested.get("status_code"), nested.get("error_code"), nested.get("code")]
            )
        for value in candidates:
            normalized = _normalize_metric_error_code(value)
            if normalized is not None:
                return normalized

    if any(isinstance(item, TimeoutError) for item in error_chain):
        return "timeout"
    if any(isinstance(item, ConnectionError) for item in error_chain):
        return "connection_error"
    return "unknown"


def _error_code(error: BaseException) -> str | None:
    body = _body(error)
    for value in (
        getattr(error, "error_code", None),
        getattr(error, "code", None),
        body.get("error_code"),
        body.get("code"),
        body.get("type"),
    ):
        if isinstance(value, str) and value:
            return value
    for value in (getattr(error, "details", None), _response_body(error)):
        reason = _structured_reason_code(value)
        if reason is not None:
            return reason
    return None


def _status_code(error: BaseException) -> int | None:
    for value in (
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
        getattr(error, "code", None),
    ):
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _structured_reason_code(value: object) -> str | None:
    pending = [value]
    seen: set[int] = set()
    status_code: str | None = None

    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            for key, item in current.items():
                if not isinstance(item, str) or _kind_from_code_value(item) is None:
                    continue
                if key == "reason":
                    return item
                if key == "status" and status_code is None:
                    status_code = item
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            if id(current) in seen:
                continue
            seen.add(id(current))
            pending.extend(current)
        elif hasattr(current, "model_dump"):
            if id(current) in seen:
                continue
            seen.add(id(current))
            try:
                pending.append(current.model_dump())
            except Exception:
                continue

    return status_code


def _kind_from_code(error: BaseException) -> ModelErrorKind | None:
    provider = getattr(error, "provider", None)
    kind = _kind_from_code_value(
        _error_code(error),
        provider if isinstance(provider, str) else None,
    )
    return kind


def _kind_from_status(status_code: int | None) -> ModelErrorKind | None:
    if status_code in _TRANSIENT_SERVICE_STATUS:
        return ModelErrorKind.TRANSIENT_SERVICE_ERROR
    if status_code in _TRANSIENT_NETWORK_STATUS:
        return ModelErrorKind.TRANSIENT_NETWORK_ERROR
    if status_code == 401:
        return ModelErrorKind.AUTHENTICATION_ERROR
    if status_code == 403:
        return ModelErrorKind.PERMISSION_DENIED
    if status_code == 404:
        return ModelErrorKind.MODEL_NOT_FOUND
    if status_code == 405 or status_code == 501:
        return ModelErrorKind.UNSUPPORTED
    if status_code == 413:
        return ModelErrorKind.INPUT_TOO_LARGE
    if status_code == 429:
        return ModelErrorKind.RATE_LIMIT_ERROR
    if status_code in {400, 422}:
        return ModelErrorKind.INVALID_REQUEST
    if status_code is not None and status_code >= 500:
        return ModelErrorKind.TRANSIENT_SERVICE_ERROR
    return None


def _best_kind(
    error: BaseException,
    *,
    class_kind: ModelErrorKind | None = None,
    include_status: bool = True,
    extra_kind: ModelErrorKind | None = None,
) -> tuple[ModelErrorKind, _EvidenceRank]:
    code_kind = _kind_from_code(error)
    if code_kind is not None:
        return code_kind, _EvidenceRank.PROVIDER_CODE
    if extra_kind is not None:
        return extra_kind, _EvidenceRank.PROVIDER_CODE
    if class_kind is not None:
        return class_kind, _EvidenceRank.SDK_CLASS
    if include_status:
        status_kind = _kind_from_status(_status_code(error))
        if status_kind is not None:
            return status_kind, _EvidenceRank.HTTP_STATUS
    return ModelErrorKind.UNKNOWN, _EvidenceRank.FALLBACK


def _sdk_kind(
    error: BaseException,
) -> tuple[ModelErrorKind, str, _EvidenceRank] | None:
    module = type(error).__module__
    name = type(error).__name__
    mro_names = {base.__name__ for base in type(error).__mro__}

    if isinstance(error, TimeoutError):
        return (
            ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "network",
            _EvidenceRank.SDK_CLASS,
        )
    if isinstance(error, ConnectionError):
        return (
            ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "network",
            _EvidenceRank.SDK_CLASS,
        )

    if module.startswith("openai"):
        provider = "openai"
        class_kinds = {
            "APITimeoutError": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "APIConnectionError": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "AuthenticationError": ModelErrorKind.AUTHENTICATION_ERROR,
            "PermissionDeniedError": ModelErrorKind.PERMISSION_DENIED,
            "NotFoundError": ModelErrorKind.MODEL_NOT_FOUND,
            "InternalServerError": ModelErrorKind.TRANSIENT_SERVICE_ERROR,
        }
        extra_kind = None
        if _kind_from_code(error) is None and _status_code(error) == 500:
            # llama.cpp reports inputs above its physical batch capacity as
            # HTTP 500 without a business code. Retrying the same input cannot help.
            message = _body(error).get("message")
            if isinstance(message, str) and re.fullmatch(
                r"input \(\d+ tokens\) is too large to process\. "
                r"increase the physical batch size \(current batch size: \d+\)",
                message,
            ):
                extra_kind = ModelErrorKind.INPUT_TOO_LARGE
        kind, evidence = _best_kind(
            error,
            class_kind=class_kinds.get(name),
            extra_kind=extra_kind,
        )
        return kind, provider, evidence

    if module.startswith("volcenginesdkarkruntime"):
        provider = "volcengine"
        class_kinds = {
            "ArkAPITimeoutError": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "ArkAPIConnectionError": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "ArkAuthenticationError": ModelErrorKind.AUTHENTICATION_ERROR,
            "ArkPermissionDeniedError": ModelErrorKind.PERMISSION_DENIED,
            "ArkNotFoundError": ModelErrorKind.MODEL_NOT_FOUND,
            "ArkInternalServerError": ModelErrorKind.TRANSIENT_SERVICE_ERROR,
            "ArkContentFilterFinishReasonError": ModelErrorKind.CONTENT_SAFETY,
        }
        kind, evidence = _best_kind(error, class_kind=class_kinds.get(name))
        return kind, provider, evidence

    if module.startswith("litellm"):
        provider = "litellm"
        class_kinds = {
            "Timeout": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "APIConnectionError": ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            "RateLimitError": ModelErrorKind.RATE_LIMIT_ERROR,
            "AuthenticationError": ModelErrorKind.AUTHENTICATION_ERROR,
            "PermissionDeniedError": ModelErrorKind.PERMISSION_DENIED,
            "NotFoundError": ModelErrorKind.MODEL_NOT_FOUND,
            "BadRequestError": ModelErrorKind.INVALID_REQUEST,
            "ContentPolicyViolationError": ModelErrorKind.CONTENT_SAFETY,
            "ContextWindowExceededError": ModelErrorKind.INPUT_TOO_LARGE,
            "ServiceUnavailableError": ModelErrorKind.TRANSIENT_SERVICE_ERROR,
            "InternalServerError": ModelErrorKind.TRANSIENT_SERVICE_ERROR,
        }
        kind, evidence = _best_kind(error, class_kind=class_kinds.get(name))
        return kind, provider, evidence

    if module.startswith("google.genai"):
        # A structured reason can distinguish quota exhaustion from request
        # throttling. When Gemini omits that reason, keep the HTTP 429 fallback
        # so the public response does not regress to INTERNAL/500.
        kind, evidence = _best_kind(error)
        return kind, "gemini", evidence

    if module.startswith("httpx"):
        if name == "HTTPStatusError":
            kind, evidence = _best_kind(error)
        elif "TimeoutException" in mro_names:
            kind = ModelErrorKind.TRANSIENT_NETWORK_ERROR
            evidence = _EvidenceRank.SDK_CLASS
        elif mro_names & {"NetworkError", "TransportError", "ProtocolError", "ProxyError"}:
            kind = ModelErrorKind.TRANSIENT_NETWORK_ERROR
            evidence = _EvidenceRank.SDK_CLASS
        else:
            kind = ModelErrorKind.UNKNOWN
            evidence = _EvidenceRank.FALLBACK
        return kind, "http", evidence

    if module.startswith("requests"):
        if name == "HTTPError":
            kind, evidence = _best_kind(error)
        elif mro_names & {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError"}:
            kind = ModelErrorKind.TRANSIENT_NETWORK_ERROR
            evidence = _EvidenceRank.SDK_CLASS
        else:
            kind = ModelErrorKind.UNKNOWN
            evidence = _EvidenceRank.FALLBACK
        return kind, "http", evidence

    return None


def model_error_from_status(
    status_code: int,
    message: str,
    *,
    provider: str,
    code: str | None = None,
) -> ModelError:
    """Build a model error from a structured HTTP or provider status."""
    kind = _kind_from_code_value(code, provider)
    return ModelError(
        kind or _kind_from_status(status_code) or ModelErrorKind.UNKNOWN,
        message,
        provider=provider,
        status_code=status_code,
        code=code,
    )


def model_error_from_code(
    code: str | int | None,
    message: str,
    *,
    provider: str,
) -> ModelError:
    """Build a model error from a provider-scoped business error code."""
    code_text = str(code) if code is not None else None
    return ModelError(
        _kind_from_code_value(code_text, provider) or ModelErrorKind.UNKNOWN,
        message,
        provider=provider,
        code=code_text,
    )


def model_error_from_exception(error: BaseException) -> ModelError | None:
    """Convert the strongest structured evidence in an exception graph."""
    selected: (
        tuple[
            _EvidenceRank,
            ModelErrorKind,
            str | None,
            BaseException,
        ]
        | None
    ) = None

    for item in model_exception_chain(error):
        carried_kind = getattr(item, "kind", None)
        if isinstance(carried_kind, ModelErrorKind) and carried_kind is not ModelErrorKind.UNKNOWN:
            provider = getattr(item, "provider", None)
            candidate = (
                _EvidenceRank.EXPLICIT_KIND,
                carried_kind,
                provider if isinstance(provider, str) else None,
                item,
            )
        elif isinstance(item, ModelError):
            kind, evidence = _best_kind(item)
            candidate = (evidence, kind, item.provider, item)
        elif isinstance(carried_kind, ModelErrorKind):
            candidate = (_EvidenceRank.FALLBACK, carried_kind, None, item)
        else:
            classified = _sdk_kind(item)
            if classified is None:
                continue
            kind, provider, evidence = classified
            candidate = (evidence, kind, provider, item)

        if selected is None or candidate[0] > selected[0]:
            selected = candidate

    if selected is None:
        return None

    _, kind, provider, source = selected
    if isinstance(source, ModelError) and source.kind is kind:
        return source
    return ModelError(
        kind,
        str(error),
        provider=provider,
        status_code=_status_code(source),
        code=_error_code(source),
    )


def model_error_kind(error: BaseException) -> ModelErrorKind | None:
    """Return the structured model kind carried by an exception chain."""
    converted = model_error_from_exception(error)
    return converted.kind if converted is not None else None


F = TypeVar("F", bound=Callable[..., Any])


def model_error_boundary(func: F) -> F:
    """Translate recognized provider failures at a public provider boundary."""

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as error:
                converted = model_error_from_exception(error)
                if converted is None or converted is error:
                    raise
                if args and isinstance(getattr(args[0], "provider", None), str):
                    converted.provider = args[0].provider
                if any(item is converted for item in model_exception_chain(error)):
                    raise converted
                raise converted from error

        return cast(F, _async_wrapper)

    @functools.wraps(func)
    def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as error:
            converted = model_error_from_exception(error)
            if converted is None or converted is error:
                raise
            if args and isinstance(getattr(args[0], "provider", None), str):
                converted.provider = args[0].provider
            if any(item is converted for item in model_exception_chain(error)):
                raise converted
            raise converted from error

    return cast(F, _sync_wrapper)


# ---------------------------------------------------------------------------
# User-facing brief: model error kind -> one short, actionable sentence
# ---------------------------------------------------------------------------

#: Generic sentence for any failure the user cannot fix by changing input.
UNKNOWN_BRIEF = "Failed to process the resource. Please try again."

_MODEL_BRIEFS: dict["ModelErrorKind", str] = {
    ModelErrorKind.AUTHENTICATION_ERROR: (
        "Model authentication failed; check the API key configuration."
    ),
    ModelErrorKind.PERMISSION_DENIED: "Permission denied for the selected model.",
    ModelErrorKind.QUOTA_EXHAUSTED: "Model service quota has been exhausted.",
    ModelErrorKind.MODEL_NOT_FOUND: "The configured model is unavailable or does not exist.",
    ModelErrorKind.UNSUPPORTED: "The model does not support this request.",
    ModelErrorKind.INVALID_REQUEST: (
        "The content could not be processed; it may be corrupted or unsupported."
    ),
    ModelErrorKind.INPUT_TOO_LARGE: "The content exceeds the model's size limit.",
    ModelErrorKind.CONTENT_SAFETY: "The content was blocked by the model's safety filter.",
    ModelErrorKind.RATE_LIMIT_ERROR: "The model service is rate-limited; please try again later.",
    ModelErrorKind.UNKNOWN: UNKNOWN_BRIEF,
}

# Transient model failures are retried internally and carry no user action.
_TRANSIENT_KINDS: frozenset[str] = frozenset(
    [
        ModelErrorKind.TRANSIENT_SERVICE_ERROR.value,
        ModelErrorKind.TRANSIENT_NETWORK_ERROR.value,
    ]
)


def brief_for(error_type: str | None) -> str | None:
    """Map a model error kind to one short user brief, or ``None`` if unclassified."""
    if not isinstance(error_type, str) or not error_type:
        return None
    for kind, brief in _MODEL_BRIEFS.items():
        if kind.value == error_type:
            return brief
    if error_type in _TRANSIENT_KINDS:
        return UNKNOWN_BRIEF
    return None
