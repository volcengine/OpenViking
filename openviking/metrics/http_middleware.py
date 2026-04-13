# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
HTTP metrics middleware.

This middleware records request-level metrics in a best-effort manner:
- Inflight gauge per route template (to avoid high cardinality per raw path).
- Request count and duration histogram by method/route/status.

Implementation notes:
- Uses HttpRequestLifecycleDataSource, which emits events; collectors write into MetricRegistry.
- All metrics calls are wrapped in try/except to ensure observability never breaks request handling.
- Route label uses the Starlette route template (e.g., "/sessions/{session_id}") when available.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from starlette.requests import Request
from starlette.responses import Response

from openviking.metrics.account_context import (
    bind_metric_account_context,
    reset_metric_account_context,
)
from openviking.metrics.datasources import HttpRequestLifecycleDataSource

logger = logging.getLogger(__name__)

_HTTP_IGNORE_ROUTES = frozenset(
    {
        "/metrics",
        "/health",
        "/ready",
    }
)


def create_http_metrics_middleware() -> Callable[[Request, Callable], Response]:
    """
    Create a Starlette-compatible middleware that records HTTP request metrics.

    The middleware emits events through `HttpRequestLifecycleDataSource`:
    - `http.inflight` updates on request start/end
    - `http.request` on request completion with status and duration

    Routes listed in `_HTTP_IGNORE_ROUTES` are intentionally skipped so internal endpoints such
    as metrics or health probes do not pollute business-facing HTTP timeseries.
    """

    async def middleware(request: Request, call_next: Callable) -> Response:
        """
        Wrap one request execution and emit best-effort inflight and request metrics events.

        The middleware never lets metrics failures affect request handling and will skip the
        entire HTTP metrics flow for routes that are explicitly ignored.
        """
        # Ignore by raw path first so internal endpoints are skipped even when no route template
        # is bound (e.g. middleware executed outside Starlette routing).
        raw_path = str(request.url.path)
        if raw_path in _HTTP_IGNORE_ROUTES:
            return await call_next(request)

        initial_route = _get_route_template(request)
        if initial_route in _HTTP_IGNORE_ROUTES:
            return await call_next(request)
        provisional_account_id = _extract_request_account_id(request)
        account_token = bind_metric_account_context(account_id=provisional_account_id)
        try:
            HttpRequestLifecycleDataSource.set_inflight(
                route=initial_route,
                value=_inflight_delta(initial_route, provisional_account_id, +1),
                account_id=provisional_account_id,
            )
        except Exception:
            _log_metrics_failure("http.inflight increment failed", route=initial_route)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            elapsed = time.perf_counter() - start
            final_route = _get_route_template(request)
            final_account_id = _extract_request_account_id(request)
            if (final_route, final_account_id) != (initial_route, provisional_account_id):
                try:
                    HttpRequestLifecycleDataSource.set_inflight(
                        route=initial_route,
                        value=_inflight_delta(initial_route, provisional_account_id, -1),
                        account_id=provisional_account_id,
                    )
                    HttpRequestLifecycleDataSource.set_inflight(
                        route=final_route,
                        value=_inflight_delta(final_route, final_account_id, +1),
                        account_id=final_account_id,
                    )
                except Exception:
                    _log_metrics_failure(
                        "http.inflight rebalance failed",
                        route=final_route,
                        account_id=final_account_id,
                    )
            try:
                HttpRequestLifecycleDataSource.record_request(
                    method=request.method,
                    route=final_route,
                    status=str(status_code),
                    duration_seconds=elapsed,
                    account_id=final_account_id,
                )
            except Exception:
                _log_metrics_failure(
                    "http.request recording failed",
                    route=final_route,
                    account_id=final_account_id,
                )
            try:
                HttpRequestLifecycleDataSource.set_inflight(
                    route=final_route,
                    value=_inflight_delta(final_route, final_account_id, -1),
                    account_id=final_account_id,
                )
            except Exception:
                _log_metrics_failure(
                    "http.inflight decrement failed",
                    route=final_route,
                    account_id=final_account_id,
                )
            reset_metric_account_context(account_token)

    return middleware


_INFLIGHT: dict[tuple[str, str | None], int] = {}
_INFLIGHT_LOCK = threading.Lock()


def _inflight_delta(route: str, account_id: str | None, delta: int) -> int:
    """
    Update and return the current in-process inflight count for a route template.

    This local counter is only used to provide a reasonable gauge value for event emission.
    It is not a correctness mechanism and is clamped to never go below zero.
    """
    key = (route, account_id)
    with _INFLIGHT_LOCK:
        v = _INFLIGHT.get(key, 0) + delta
        if v < 0:
            v = 0
        if v == 0:
            _INFLIGHT.pop(key, None)
        else:
            _INFLIGHT[key] = v
        return v


def _get_route_template(request: Request) -> str:
    """
    Resolve a low-cardinality route identifier for labeling request metrics.

    Prefer the Starlette route template (e.g. `/sessions/{session_id}`) when available, and
    fall back to a fixed sentinel when the request is not bound to a recognized route template.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    # Never label by raw path here: it may contain IDs and explode cardinality.
    return "/__unmatched__"


def _extract_request_account_id(request: Request) -> str | None:
    """
    Return the best request-scoped account id currently available for metrics labeling.

    Only trust the authenticated request state (`request.state.metric_account_id`).

    The `/metrics` endpoint is intentionally best-effort and must not let unauthenticated or
    rejected traffic control tenant labels via raw headers. When the authenticated account id
    is not available yet, return `None` so collector-side policy can map it to `__unknown__`.
    """
    state_account_id = getattr(request.state, "metric_account_id", None)
    if state_account_id:
        return str(state_account_id)
    return None


def _log_metrics_failure(message: str, *, route: str, account_id: str | None = None) -> None:
    """
    Emit a debug log for best-effort metrics failures without affecting request handling.

    This middleware intentionally treats observability as a side channel. The helper keeps
    failure reporting centralized so all swallowed exceptions still leave a trace when debug
    logging is enabled.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug(
        "http metrics write failed: %s",
        message,
        extra={
            "route": route,
            "account_id": account_id,
        },
        exc_info=True,
    )
