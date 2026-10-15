# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Provider-neutral model API errors."""

from __future__ import annotations

import functools
import inspect
import re
from enum import Enum
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
    "BILLINGHARDLIMITREACHED": ModelErrorKind.QUOTA_EXHAUSTED,
    "ACCOUNTOVERDUE": ModelErrorKind.QUOTA_EXHAUSTED,
    # Request rate limits.
    "RATELIMITEXCEEDED": ModelErrorKind.RATE_LIMIT_ERROR,
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

_TRANSIENT_SERVICE_STATUS = {409, 500, 502, 503}
_TRANSIENT_NETWORK_STATUS = {408, 504}


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _normalized_code(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return "".join(character for character in value.upper() if character.isalnum())


def _body(error: BaseException) -> dict[str, Any]:
    value = getattr(error, "body", None)
    if not isinstance(value, dict):
        response = getattr(error, "response", None)
        try:
            value = response.json() if response is not None else None
        except Exception:
            value = None
    if isinstance(value, dict):
        nested = value.get("error")
        if isinstance(nested, dict):
            return {**value, **nested}
        return value
    return {}


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


def _structured_reasons(value: object) -> set[str]:
    reasons: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"reason", "status"} and isinstance(item, str):
                normalized = _normalized_code(item)
                if normalized:
                    reasons.add(normalized)
            reasons.update(_structured_reasons(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            reasons.update(_structured_reasons(item))
    elif hasattr(value, "model_dump"):
        reasons.update(_structured_reasons(value.model_dump()))
    return reasons


def _kind_from_code(error: BaseException) -> ModelErrorKind | None:
    normalized = _normalized_code(_error_code(error))
    if normalized in _CODE_KINDS:
        return _CODE_KINDS[normalized]

    details = getattr(error, "details", None)
    reasons = _structured_reasons(details)
    if reasons & {"QUOTAEXCEEDED", "BILLINGDISABLED"}:
        return ModelErrorKind.QUOTA_EXHAUSTED
    if reasons & {"RATELIMITEXCEEDED", "SERVINGCAPACITYEXCEEDED"}:
        return ModelErrorKind.RATE_LIMIT_ERROR
    if reasons & {"APIKEYINVALID", "UNAUTHENTICATED"}:
        return ModelErrorKind.AUTHENTICATION_ERROR
    return None


def _kind_from_status(status_code: int | None) -> ModelErrorKind | None:
    if status_code in _TRANSIENT_SERVICE_STATUS:
        return ModelErrorKind.TRANSIENT_SERVICE_ERROR
    if status_code in _TRANSIENT_NETWORK_STATUS:
        return ModelErrorKind.TRANSIENT_NETWORK_ERROR
    if status_code == 401:
        return ModelErrorKind.AUTHENTICATION_ERROR
    if status_code == 403:
        return ModelErrorKind.PERMISSION_DENIED
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


def _sdk_kind(error: BaseException) -> tuple[ModelErrorKind, str] | None:
    module = type(error).__module__
    name = type(error).__name__
    mro_names = {base.__name__ for base in type(error).__mro__}

    if isinstance(error, TimeoutError):
        return ModelErrorKind.TRANSIENT_NETWORK_ERROR, "network"
    if isinstance(error, ConnectionError):
        return ModelErrorKind.TRANSIENT_NETWORK_ERROR, "network"

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
        kind = _kind_from_code(error)
        if kind is None and _status_code(error) == 500:
            # llama.cpp reports inputs above its physical batch capacity as
            # HTTP 500 without a business code. Retrying the same input cannot help.
            message = _body(error).get("message")
            if isinstance(message, str) and re.fullmatch(
                r"input \(\d+ tokens\) is too large to process\. "
                r"increase the physical batch size \(current batch size: \d+\)",
                message,
            ):
                kind = ModelErrorKind.INPUT_TOO_LARGE
        kind = kind or class_kinds.get(name) or _kind_from_status(_status_code(error))
        return kind or ModelErrorKind.UNKNOWN, provider

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
        kind = (
            _kind_from_code(error)
            or class_kinds.get(name)
            or _kind_from_status(_status_code(error))
        )
        return kind or ModelErrorKind.UNKNOWN, provider

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
        return _kind_from_code(error) or class_kinds.get(name, ModelErrorKind.UNKNOWN), provider

    if module.startswith("google.genai"):
        kind = _kind_from_code(error)
        status_code = _status_code(error)
        # RESOURCE_EXHAUSTED is ambiguous unless Google supplies a reason.
        if status_code == 429 and kind is None:
            kind = ModelErrorKind.UNKNOWN
        return kind or _kind_from_status(status_code) or ModelErrorKind.UNKNOWN, "gemini"

    if module.startswith("httpx"):
        if name == "HTTPStatusError":
            kind = _kind_from_code(error) or _kind_from_status(_status_code(error))
        elif "TimeoutException" in mro_names:
            kind = ModelErrorKind.TRANSIENT_NETWORK_ERROR
        elif mro_names & {"NetworkError", "TransportError", "ProtocolError", "ProxyError"}:
            kind = ModelErrorKind.TRANSIENT_NETWORK_ERROR
        else:
            kind = None
        return kind or ModelErrorKind.UNKNOWN, "http"

    if module.startswith("requests"):
        if name == "HTTPError":
            kind = _kind_from_code(error) or _kind_from_status(_status_code(error))
        elif mro_names & {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError"}:
            kind = ModelErrorKind.TRANSIENT_NETWORK_ERROR
        else:
            kind = None
        return kind or ModelErrorKind.UNKNOWN, "http"

    return None


def model_error_from_status(
    status_code: int,
    message: str,
    *,
    provider: str,
    code: str | None = None,
) -> ModelError:
    """Build a model error from a structured HTTP or provider status."""
    normalized = _normalized_code(code)
    kind = _CODE_KINDS.get(normalized) if normalized else None
    return ModelError(
        kind or _kind_from_status(status_code) or ModelErrorKind.UNKNOWN,
        message,
        provider=provider,
        status_code=status_code,
        code=code,
    )


def model_error_from_exception(error: BaseException) -> ModelError | None:
    """Convert a recognized SDK/API exception at the provider boundary."""
    for item in _exception_chain(error):
        if isinstance(item, ModelError):
            return item
        classified = _sdk_kind(item)
        if classified is None:
            continue
        kind, provider = classified
        return ModelError(
            kind,
            str(error),
            provider=provider,
            status_code=_status_code(item),
            code=_error_code(item),
        )
    return None


def model_error_kind(error: BaseException) -> ModelErrorKind | None:
    """Return the structured model kind carried by an exception chain."""
    for item in _exception_chain(error):
        kind = getattr(item, "kind", None)
        if isinstance(kind, ModelErrorKind):
            return kind
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
                if any(item is converted for item in _exception_chain(error)):
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
            if any(item is converted for item in _exception_chain(error)):
                raise converted
            raise converted from error

    return cast(F, _sync_wrapper)
