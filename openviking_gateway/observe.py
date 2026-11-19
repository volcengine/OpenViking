# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""The record every model-route request writes, and the reasons its errors carry."""

import contextlib
import logging
import re
import time
import uuid
from typing import NamedTuple

import orjson
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from .client import VikingError
from .storage import STORE_ERRORS
from .tool_protocols.common import ToolLoopError

logger = logging.getLogger(__name__)


class Reason(NamedTuple):
    # 0: the status of the error behind the reason.
    status: int
    origin: str
    error_class: str


REASONS = {
    "gateway_key_invalid": Reason(401, "gateway", "api_key_invalid"),
    "openviking_key_invalid": Reason(401, "openviking", "api_key_invalid"),
    "openviking_identity_missing": Reason(401, "openviking", "openviking_unavailable"),
    "openviking_not_ordered": Reason(402, "openviking", "quota_or_order"),
    "openviking_forbidden": Reason(403, "openviking", "api_key_invalid"),
    "root_key_not_allowed": Reason(403, "openviking", "config_error"),
    "trusted_mode_unsupported": Reason(403, "openviking", "openviking_unavailable"),
    "oauth_credential_unsupported": Reason(403, "gateway", "config_error"),
    "openviking_rate_limited": Reason(429, "openviking", "quota_or_order"),
    "openviking_error": Reason(0, "openviking", "openviking_unavailable"),
    "openviking_version_mismatch": Reason(503, "openviking", "openviking_unavailable"),
    "openviking_unavailable": Reason(503, "openviking", "openviking_unavailable"),
    "openviking_timeout": Reason(503, "openviking", "openviking_unavailable"),
    "body_too_large": Reason(413, "gateway", "config_error"),
    "compressed_body_unsupported": Reason(415, "gateway", "config_error"),
    "websocket_unsupported": Reason(426, "gateway", "config_error"),
    "model_missing": Reason(400, "gateway", "config_error"),
    "model_not_allowed": Reason(403, "gateway", "config_error"),
    "context_override_invalid": Reason(400, "gateway", "config_error"),
    "context_override_not_allowed": Reason(403, "gateway", "config_error"),
    "peer_invalid": Reason(400, "gateway", "config_error"),
    "policy_unavailable": Reason(403, "gateway", "config_error"),
    "upstream_url_not_allowed": Reason(403, "gateway", "gateway_internal"),
    "no_matching_upstream": Reason(404, "gateway", "gateway_internal"),
    "upstream_endpoint_mismatch": Reason(404, "gateway", "config_error"),
    "response_unknown": Reason(404, "gateway", "config_error"),
    "response_upstream_disallowed": Reason(403, "gateway", "config_error"),
    "upstream_key_missing": Reason(401, "gateway", "config_error"),
    "coding_plan_disabled": Reason(403, "gateway", "config_error"),
    "store_unavailable": Reason(503, "gateway", "gateway_internal"),
    "gateway_overloaded": Reason(503, "gateway", "gateway_internal"),
    "internal_error": Reason(500, "gateway", "gateway_internal"),
    "upstream_timeout": Reason(504, "upstream", "timeout"),
    "upstream_unreachable": Reason(502, "upstream", "model_error"),
    "upstream_disconnected": Reason(502, "upstream", "model_error"),
    "tool_upstream_invalid": Reason(502, "upstream", "model_error"),
    "tool_upstream_incomplete": Reason(502, "upstream", "model_error"),
    "tool_upstream_stream_error": Reason(502, "upstream", "model_error"),
    "tool_call_invalid": Reason(502, "upstream", "model_error"),
    "tool_loop_limit": Reason(502, "gateway", "gateway_internal"),
    "tool_loop_timeout": Reason(504, "gateway", "timeout"),
    # Recorded only: the provider's own error, relayed, and a client that went away.
    "upstream_error": Reason(0, "upstream", "model_error"),
    "client_disconnected": Reason(499, "client", "client_cancelled"),
}
# The OpenViking reasons a model route reports under another name.
VIKING_REASONS = {
    "openviking_http_401": "openviking_key_invalid",
    "openviking_http_402": "openviking_not_ordered",
    "openviking_http_403": "openviking_forbidden",
    "openviking_http_429": "openviking_rate_limited",
}
VIKING_DETAILS = {
    "openviking_unavailable": "OpenViking is unreachable.",
    "openviking_unreachable": "OpenViking is unreachable.",
    "openviking_http_401": "OpenViking rejected the API key.",
    "openviking_http_402": "OpenViking service is not ordered for this API key.",
    "openviking_identity_missing": "OpenViking returned no identity for the API key.",
    "trusted_mode_unsupported": "OpenViking Gateway does not support trusted authentication.",
    "root_key_not_allowed": "A root API key cannot back a gateway key; use a user's API key.",
    "openviking_version_mismatch": "OpenViking Server is older than this gateway requires.",
    "openviking_timeout": "OpenViking did not answer in time.",
}
INTERNAL_DETAIL = "The gateway failed to handle this request."
STORE_DETAIL = "The gateway's state store is unavailable."
ERROR_CODE = re.compile(r"[A-Za-z0-9._-]{1,64}")
REQUEST_ID = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
# Headers a fronting service sets, recorded only with ``trusted_host_headers``.
HOST_HEADERS = {
    "resource_id": ("x-openviking-resource-id", re.compile(r"[A-Za-z0-9._:-]{1,128}")),
    "request_source": ("x-openviking-request-source", re.compile(r"[a-z0-9_-]{1,32}")),
}
# The cause is transient, so a client may retry at once. A host that would send the request
# elsewhere reads X-OpenViking-Upstream-Attempted instead.
RETRY_AFTER = {"store_unavailable", "gateway_overloaded"}


class GatewayError(HTTPException):
    """A model-route error with a reason from ``REASONS``."""

    def __init__(self, reason, detail, *, status=None, headers=None):
        super().__init__(
            status or REASONS[reason].status, {"reason": reason, "detail": detail}, headers
        )


def classify(error):
    """The status, reason, origin, detail and extra headers a model route answers with."""
    if isinstance(error, StarletteHTTPException):
        if isinstance(error.detail, dict):
            reason, detail = error.detail.get("reason", ""), error.detail.get("detail", "")
        else:
            # Model routes raise GatewayError; this keeps a stray raise identifiable.
            reason, detail = f"gateway_http_{error.status_code}", error.detail
        origin = REASONS[reason].origin if reason in REASONS else "gateway"
        return error.status_code, reason, origin, detail, dict(error.headers or {})
    if isinstance(error, VikingError):
        if error.reason in REASONS:
            reason = error.reason
        elif error.reason in VIKING_REASONS:
            reason = VIKING_REASONS[error.reason]
        elif error.reason.startswith("openviking_http_5"):
            reason = "openviking_unavailable"
        else:
            reason = "openviking_error"
        detail = VIKING_DETAILS.get(error.reason) or VIKING_DETAILS.get(
            reason, "OpenViking request failed."
        )
        return REASONS[reason].status or error.status, reason, "openviking", detail, {}
    if isinstance(error, ToolLoopError):
        return error.status, error.code, REASONS[error.code].origin, str(error), {}
    if isinstance(error, ClientDisconnect):
        return 499, "client_disconnected", "client", "The client closed the request.", {}
    if isinstance(error, STORE_ERRORS):
        logger.error(
            "gateway state store unavailable; request failed error_type=%s",
            type(error).__name__,
            extra={"error_type": type(error).__name__},
        )
        return 503, "store_unavailable", "gateway", STORE_DETAIL, {}
    logger.error(
        "gateway request failed error_type=%s",
        type(error).__name__,
        extra={"error_type": type(error).__name__},
    )
    return 500, "internal_error", "gateway", INTERNAL_DETAIL, {}


def error_code(value):
    """The code of a provider error body or event, never its message."""
    if isinstance(value, (bytes, str)):
        if len(value) > 16384:
            return None
        try:
            value = orjson.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    detail = value.get("error")
    codes = [detail.get("code"), detail.get("type")] if isinstance(detail, dict) else []
    return next(
        (c for c in [*codes, value.get("code")] if isinstance(c, str) and ERROR_CODE.fullmatch(c)),
        None,
    )


def degrade(metrics, value, *, primary=True, keep=False):
    """Note a degradation; ``degradation`` keeps the last primary one, as it always has."""
    if primary:
        if keep:
            metrics.setdefault("degradation", value)
        else:
            metrics["degradation"] = value
    listed = metrics.setdefault("degradations", [])
    if value not in listed:
        listed.append(value)


def endpoint(path):
    """The record's name for a model-route path below the mount."""
    path = "/" + path.lstrip("/")
    for prefix in ("/api/v3/", "/api/compatible/v1/"):
        if path.startswith(prefix):
            path = "/v1/" + path.removeprefix(prefix)
            break
    if path.startswith("/v1/responses/"):
        return "responses_item"
    return {
        "/v1/messages": "messages",
        "/v1/messages/count_tokens": "count_tokens",
        "/v1/chat/completions": "chat",
        "/v1/responses": "responses",
    }.get(path, "other")


def elapsed_ms(since):
    return round((time.monotonic() - since) * 1000, 2)


class RequestRecord:
    """The one record a model-route request writes, whichever way it ends."""

    def __init__(self, app, request, *, recorded=True):
        self.app, self.recorded = app, recorded
        # Set right before the first upstream request, a compaction summary included.
        self.attempted = self.streaming = False
        self.stats = getattr(getattr(app, "state", None), "stats", None) if recorded else None
        if self.stats:
            self.stats.inflight_requests += 1
        # A response object calls ProxyRequest.finish(), which writes the record.
        self.deferred = False
        self.account = self.user_id = None
        self.started, self.emitted = time.monotonic(), False
        self.metrics = {"request_id": uuid.uuid4().hex, "received_at": time.time()}
        if request is None:
            return
        inbound = request.headers.get("x-openviking-request-id")
        if inbound is not None:
            if REQUEST_ID.fullmatch(inbound):
                self.metrics["request_id"] = inbound
            else:
                self.metrics["request_id_rejected"] = True
        path = request.url.path.removeprefix(request.scope.get("root_path", ""))
        self.metrics.update(method=request.method, endpoint=endpoint(path))
        if app.state.config.trusted_host_headers:
            for field, (name, pattern) in HOST_HEADERS.items():
                if (value := request.headers.get(name)) and pattern.fullmatch(value):
                    self.metrics[field] = value

    @contextlib.contextmanager
    def stage(self, name):
        started = time.monotonic()
        try:
            yield
        finally:
            self.metrics[name + "_ms"] = elapsed_ms(started)

    def identify(self, credential):
        self.account, self.user_id = credential["account"], credential["user_id"]
        self.metrics.update(
            openviking_account=credential.get("openviking_account", credential["account"]),
            credential_id=credential["id"],
        )

    def merge(self, metrics):
        """``dict.update``, except that ``degradations`` adds up without duplicates."""
        listed = [*self.metrics.get("degradations", []), *metrics.get("degradations", [])]
        self.metrics.update(metrics)
        if listed:
            self.metrics["degradations"] = list(dict.fromkeys(listed))

    def fail(self, status, reason, origin=None):
        known = REASONS.get(reason)
        self.metrics.update(
            status=status,
            error_reason=reason,
            error_origin=origin or (known.origin if known else "gateway"),
            error_class=known.error_class if known else "gateway_internal",
        )
        if reason == "client_disconnected":
            self.metrics["client_disconnected"] = True

    def stream_started(self):
        """Count the request as a stream in flight once its body starts."""
        if self.stats and not self.streaming:
            self.stats.inflight_streams += 1
        self.streaming = True

    def stream_failed(self, status, reason, error=None):
        """Note why a response body that started did not end normally; the first reason wins."""
        if "stream_error" in self.metrics:
            return
        self.metrics["stream_error"] = reason
        if code := error_code(getattr(error, "content", None)):
            self.metrics["upstream_error_code"] = code
        self.fail(status, reason)

    def headers(self, error=False):
        """Response headers; ``error`` adds the ones of a status the gateway produced itself."""
        headers = {"X-OpenViking-Request-Id": self.metrics["request_id"]}
        if degradations := self.metrics.get("degradations"):
            headers["X-OpenViking-Degraded"] = ",".join(degradations)
        if error:
            reason = self.metrics["error_reason"]
            headers.update(
                {
                    "X-OpenViking-Error-Reason": reason,
                    "X-OpenViking-Error-Origin": self.metrics["error_origin"],
                    "X-OpenViking-Upstream-Attempted": "1" if self.attempted else "0",
                }
            )
            if reason in RETRY_AFTER:
                headers["Retry-After"] = "1"
        return headers

    def decorate(self, response):
        """Add the request ID and the degradations so far to any response."""
        response.headers.update(self.headers())
        return response

    def error_response(self, error):
        status, reason, origin, detail, headers = classify(error)
        self.fail(status, reason, origin)
        body = {"reason": reason, "detail": detail, "request_id": self.metrics["request_id"]}
        if reason == "openviking_version_mismatch":
            body["required_version"] = self.app.state.config.min_server_version
        return JSONResponse(body, status_code=status, headers={**headers, **self.headers(True)})

    async def emit(self):
        """Write the record once; a failing sink is logged, never raised."""
        if self.emitted or not self.recorded:
            return
        self.emitted = True
        self.metrics.update(
            event="request",
            schema=2,
            upstream_attempted=self.attempted,
            total_ms=elapsed_ms(self.started),
        )
        if stats := self.stats:
            stats.inflight_requests -= 1
            stats.inflight_streams -= self.streaming
            stats.recorded(self.metrics)
        state = self.app.state
        # The store keeps records per account; one before authentication has none.
        if self.account is None and state.config.request_log_sink == "store":
            if stats:
                stats.records_unlogged += 1
            return
        try:
            await state.management.log(self.account, self.metrics, user_id=self.user_id)
        except Exception:
            logger.info(
                "gateway request record write failed; preserving response", extra={"degraded": True}
            )
            if stats:
                stats.records_failed += 1
