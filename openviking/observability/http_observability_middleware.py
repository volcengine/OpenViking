"""Unified HTTP observability middleware.

This module intentionally owns both HTTP metrics and HTTP tracing logic.

Rationale:
- A single middleware entry point avoids ordering issues between separate Starlette/FastAPI
  middlewares (e.g. authentication mutating request-scoped fields that metrics need).
- Route template resolution happens after routing, so both tracing and metrics need a shared
  convention for when and how to finalize `http_route`.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ContextManager, Optional

from starlette.requests import Request
from starlette.responses import Response

from openviking.metrics.datasources import HttpRequestLifecycleDataSource
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.telemetry.span_models import RootSpanAttributes, create_root_span_attributes

# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.propagate import extract
    from opentelemetry.trace import Status, StatusCode
except ImportError:
    otel_trace = None
    extract = None
    Status = None
    StatusCode = None

logger = logging.getLogger(__name__)

_HTTP_IGNORE_ROUTES = frozenset(
    {
        "/metrics",
        "/health",
        "/ready",
    }
)


class ShardedInflightCounter:
    """
    Sharded inflight counter to reduce lock contention under high concurrency.

    This class splits the key space into multiple shards, each with its own lock.
    This reduces lock contention compared to a single global lock.

    The number of shards should be a power of 2 for efficient hash-based sharding.
    """

    def __init__(self, num_shards: int = 16):
        """
        Initialize the sharded inflight counter.

        Args:
            num_shards: Number of shards to use. Should be a power of 2.
                Default is 16, which provides good balance between
                memory overhead and lock contention reduction.
        """
        self._num_shards = num_shards
        self._shards: list[dict] = [{} for _ in range(num_shards)]
        self._locks: list[threading.Lock] = [threading.Lock() for _ in range(num_shards)]

    def _get_shard_index(self, key: tuple) -> int:
        """
        Calculate the shard index for a given key.

        Uses the built-in hash function to distribute keys across shards.

        Args:
            key: The key to hash (route, account_id tuple).

        Returns:
            The shard index (0 to num_shards - 1).
        """
        return hash(key) % self._num_shards

    def increment(self, route: str, account_id: Optional[str] = None) -> int:
        """
        Increment the inflight count for a route and account_id.

        Args:
            route: The route template.
            account_id: The account identifier, or None.

        Returns:
            The new inflight count after increment.
        """
        key = (route, account_id)
        shard_idx = self._get_shard_index(key)

        with self._locks[shard_idx]:
            shard = self._shards[shard_idx]
            shard[key] = shard.get(key, 0) + 1
            return shard[key]

    def decrement(self, route: str, account_id: Optional[str] = None) -> int:
        """
        Decrement the inflight count for a route and account_id.

        The count is clamped to never go below zero.

        Args:
            route: The route template.
            account_id: The account identifier, or None.

        Returns:
            The new inflight count after decrement (clamped to >= 0).
        """
        key = (route, account_id)
        shard_idx = self._get_shard_index(key)

        with self._locks[shard_idx]:
            shard = self._shards[shard_idx]
            if key in shard:
                shard[key] -= 1
                if shard[key] <= 0:
                    del shard[key]
                    return 0
                return shard[key]
            return 0

    def get(self, route: str, account_id: Optional[str] = None) -> int:
        """
        Get the current inflight count for a route and account_id.

        This method uses lock-free reading for better performance under high concurrency.
        In CPython, a single dict.get() call is atomic due to the GIL, so no lock is needed.

        Args:
            route: The route template.
            account_id: The account identifier, or None.

        Returns:
            The current inflight count (0 if not found).
        """
        key = (route, account_id)
        shard_idx = self._get_shard_index(key)

        # Lock-free read: single dict.get() is atomic in CPython
        # If the read fails for any reason, fall back to locked read
        try:
            return self._shards[shard_idx].get(key, 0)
        except Exception:
            # Fall back to locked read if lock-free read fails
            with self._locks[shard_idx]:
                return self._shards[shard_idx].get(key, 0)

    def get_all(self) -> dict[tuple, int]:
        """
        Get all inflight counts across all shards.

        This method first attempts lock-free reading for better performance.
        If lock-free reading fails (e.g., dictionary modified during iteration),
        it falls back to locked reading.

        Note: Lock-free reading may return slightly stale or inconsistent data
        under high concurrency, but this is acceptable for monitoring purposes.

        Returns:
            A dictionary of all (route, account_id) -> count mappings.
        """
        result: dict[tuple, int] = {}

        # First attempt: lock-free reading
        try:
            for i in range(self._num_shards):
                # dict.copy() is atomic in CPython for small dicts
                # but may fail if the dict is being modified
                shard_copy = dict(self._shards[i])
                result.update(shard_copy)
            return result
        except Exception:
            # Lock-free read failed, fall back to locked reading
            pass

        # Fallback: locked reading
        for i in range(self._num_shards):
            with self._locks[i]:
                result.update(self._shards[i])
        return result


# Global sharded inflight counter instance
# Using 16 shards provides good balance between memory and contention reduction
_INFLIGHT_COUNTER = ShardedInflightCounter(num_shards=16)


def maybe_start_root_span(
    request: Request, root_attrs: RootSpanAttributes
) -> Optional[ContextManager[Any]]:
    """
    Return a root span context manager when OTel is available.

    Creates a server span for the incoming HTTP request, extracting trace context
    from request headers if present.

    Args:
        request: The incoming HTTP request.
        root_attrs: The root span attributes.

    Returns:
        A span context manager if OTel is available and span creation succeeds,
        None otherwise.
    """
    if otel_trace is None or extract is None:
        logger.debug("OTel trace or extract not available, skipping root span creation")
        return None

    try:
        tracer = otel_trace.get_tracer(__name__)
        if tracer is None:
            logger.warning("OTel tracer is None, cannot create root span")
            return None

        carrier = dict(request.headers)
        context = extract(carrier)
        span_name = f"{request.method} {root_attrs.http_route}"

        return tracer.start_as_current_span(
            name=span_name,
            context=context,
            kind=otel_trace.SpanKind.SERVER,
        )

    except ImportError as e:
        logger.warning("OTel import error when creating root span: %s", str(e))
        return None

    except (TypeError, ValueError, AttributeError) as e:
        logger.warning("OTel configuration error when creating root span: %s", str(e))
        return None

    except Exception as e:
        logger.error(
            "Unexpected error when creating root span: %s",
            str(e),
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )
        return None


def maybe_apply_root_span_attributes(root_attrs: RootSpanAttributes) -> None:
    """
    Best-effort sync of root attributes into the current OTel span.

    Sets the root span attributes (request_id, http_method, http_route, account_id, etc.)
    on the currently active OTel span.

    Args:
        root_attrs: The root span attributes to apply.
    """
    if otel_trace is None:
        logger.debug("OTel trace not available, skipping root span attributes application")
        return

    try:
        current_span = otel_trace.get_current_span()
        if current_span is None:
            logger.debug("No current span found, skipping root span attributes application")
            return

        if not current_span.is_recording():
            logger.debug("Current span is not recording, skipping root span attributes application")
            return

        current_span.set_attributes(root_attrs.to_otel_attributes())

    except (TypeError, ValueError, AttributeError) as e:
        logger.warning("Error applying root span attributes: %s", str(e))

    except Exception as e:
        logger.error(
            "Unexpected error applying root span attributes: %s",
            str(e),
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


def maybe_apply_root_span_response(root_attrs: RootSpanAttributes) -> None:
    """
    Best-effort sync of HTTP response information into the current root span.

    Sets the HTTP status code and updates the span status based on the response:
    - 5xx: ERROR status
    - 4xx: UNSET status
    - 2xx/3xx: OK status (implicit)

    Args:
        root_attrs: The root span attributes containing the HTTP status code.
    """
    if otel_trace is None or Status is None or StatusCode is None:
        logger.debug(
            "OTel trace or Status/StatusCode not available, skipping response state application"
        )
        return

    try:
        current_span = otel_trace.get_current_span()
        if current_span is None or not current_span.is_recording():
            logger.debug("No active recording span found, skipping response state application")
            return

        current_span.set_attributes(root_attrs.to_otel_attributes())

        status_code = int(root_attrs.http_status_code or 500)

        if status_code >= 500:
            current_span.set_status(Status(StatusCode.ERROR, description=f"HTTP {status_code}"))
        elif status_code >= 400:
            current_span.set_status(Status(StatusCode.UNSET))
        # 2xx/3xx implicitly get OK status

    except (TypeError, ValueError, AttributeError) as e:
        logger.warning("Error applying root span response state: %s", str(e))

    except Exception as e:
        logger.error(
            "Unexpected error applying root span response state: %s",
            str(e),
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


def maybe_apply_root_span_error(root_attrs: RootSpanAttributes, exc: Exception) -> None:
    """
    Best-effort exception recording for the current root span.

    Records the exception on the currently active OTel span and sets the span
    status to ERROR.

    Args:
        root_attrs: The root span attributes.
        exc: The exception to record.
    """
    if otel_trace is None or Status is None or StatusCode is None:
        logger.debug(
            "OTel trace or Status/StatusCode not available, skipping error state application"
        )
        return

    try:
        current_span = otel_trace.get_current_span()
        if current_span is None or not current_span.is_recording():
            logger.debug("No active recording span found, skipping error state application")
            return

        current_span.set_attributes(root_attrs.to_otel_attributes())
        current_span.record_exception(exc)
        current_span.set_status(Status(StatusCode.ERROR, description=str(exc)))

    except (TypeError, ValueError, AttributeError) as e:
        logger.warning("Error applying root span error state: %s", str(e))

    except Exception as e:
        logger.error(
            "Unexpected error applying root span error state: %s",
            str(e),
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


def _get_route_template(request: Request) -> str:
    """
    Resolve a shared low-cardinality route template for request observability.

    Extracts the route template from the request scope after routing has occurred.
    If routing has not yet occurred, returns "/__unmatched__".

    Args:
        request: The incoming HTTP request.

    Returns:
        The route template string (e.g., "/api/v1/sessions/{session_id}") if routing
        has occurred, otherwise "/__unmatched__".
    """
    try:
        route = request.scope.get("route")
        path = getattr(route, "path", None)
        if path:
            return str(path)
        return "/__unmatched__"
    except (TypeError, AttributeError) as e:
        logger.debug("Error getting route template: %s", str(e))
        return "/__unmatched__"
    except Exception as e:
        logger.error(
            "Unexpected error getting route template: %s",
            str(e),
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )
        return "/__unmatched__"


def _maybe_update_route_and_span_name(
    *,
    request: Request,
    root_attrs: Any,
    span: Any,
) -> None:
    """
    Update `root_attrs.http_route` and the active span name after routing.

    Starlette/FastAPI perform route matching in the downstream app (i.e. inside `call_next`).
    This means `request.scope["route"]` is often unavailable before `call_next` executes.

    We intentionally:
    - keep `url_path` as the raw request path (always available),
    - fill `http_route` with a low-cardinality route template once routing completes.

    Args:
        request: The incoming HTTP request.
        root_attrs: The root span attributes to update.
        span: The active OTel span (if any).
    """
    try:
        final_route = _get_route_template(request)

        if final_route and final_route != "/__unmatched__":
            if getattr(root_attrs, "http_route", None) != final_route:
                root_attrs.http_route = final_route

            # Keep span name aligned with the finalized route template for better aggregation.
            if span is not None:
                update_name = getattr(span, "update_name", None)
                if callable(update_name):
                    update_name(f"{request.method} {final_route}")

    except (TypeError, ValueError, AttributeError) as e:
        logger.warning("Error updating route and span name: %s", str(e))

    except Exception as e:
        logger.error(
            "Unexpected error updating route and span name: %s",
            str(e),
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


def should_skip_http_metrics(request: Request) -> bool:
    """
    Return whether the request should skip HTTP metrics/tracing entirely.

    Checks if the request path matches any of the ignore routes (health checks,
    metrics endpoint, etc.).

    Args:
        request: The incoming HTTP request.

    Returns:
        True if the request should be skipped, False otherwise.
    """
    try:
        raw_path = str(request.url.path)
        if raw_path in _HTTP_IGNORE_ROUTES:
            return True

        route_template = _get_route_template(request)
        return route_template in _HTTP_IGNORE_ROUTES

    except Exception as e:
        logger.warning("Error checking if should skip HTTP metrics: %s", str(e))
        # Default to not skipping on error
        return False


@dataclass(slots=True)
class HTTPMetricsStartState:
    """Capture the initial metrics labels used when a request starts."""

    route: str
    account_id: Optional[str]


def _inflight_delta(route: str, account_id: Optional[str], delta: int) -> int:
    """
    Update and return the current in-process inflight count for a route template.

    This function uses a sharded counter to reduce lock contention under high concurrency.
    The local counter is only used to provide a reasonable gauge value for event emission.
    It is not a correctness mechanism and is clamped to never go below zero.

    Args:
        route: The route template.
        account_id: The account identifier, or None.
        delta: The change to apply (+1 for increment, -1 for decrement).

    Returns:
        The new inflight count after the delta is applied.
    """
    if delta > 0:
        return _INFLIGHT_COUNTER.increment(route, account_id)
    elif delta < 0:
        return _INFLIGHT_COUNTER.decrement(route, account_id)
    else:
        return _INFLIGHT_COUNTER.get(route, account_id)


def _log_metrics_failure(
    message: str,
    *,
    route: str,
    account_id: Optional[str] = None,
    error: Optional[Exception] = None,
) -> None:
    """
    Emit a log for best-effort metrics failures without affecting request handling.

    This middleware intentionally treats observability as a side channel. The helper keeps
    failure reporting centralized so all swallowed exceptions still leave a trace.

    Logs at WARNING level for known error types, and ERROR level for unexpected errors.
    Stack traces are only included when debug logging is enabled.

    Args:
        message: The error message.
        route: The route template.
        account_id: The account identifier, or None.
        error: The exception that occurred, or None.
    """
    log_level = logging.WARNING if error else logging.DEBUG

    extra = {
        "route": route,
        "account_id": account_id,
    }

    if error:
        logger.log(
            log_level,
            "http metrics write failed: %s - %s",
            message,
            str(error),
            extra=extra,
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )
    else:
        logger.log(
            log_level,
            "http metrics write failed: %s",
            message,
            extra=extra,
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


def apply_http_metrics_start(
    *,
    request: Request,
    root_attrs: RootSpanAttributes,
) -> HTTPMetricsStartState:
    """
    Record the initial inflight sample and store the start-state on the request.

    Called at the beginning of request handling to increment the inflight counter
    and capture the initial route/account_id for later rebalancing.

    Args:
        request: The incoming HTTP request.
        root_attrs: The root span attributes.

    Returns:
        The HTTPMetricsStartState containing the initial route and account_id.
    """
    initial_route = root_attrs.http_route
    initial_account_id = root_attrs.account_id

    state = HTTPMetricsStartState(route=initial_route, account_id=initial_account_id)
    request.state.http_metrics_start_state = state

    try:
        HttpRequestLifecycleDataSource.set_inflight(
            route=initial_route,
            value=_inflight_delta(initial_route, initial_account_id, +1),
            account_id=initial_account_id,
        )

    except (TypeError, ValueError, AttributeError) as e:
        _log_metrics_failure(
            "http.inflight increment failed",
            route=initial_route,
            account_id=initial_account_id,
            error=e,
        )

    except Exception as e:
        logger.error(
            "Unexpected error in http.inflight increment: %s",
            str(e),
            extra={
                "route": initial_route,
                "account_id": initial_account_id,
            },
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )

    return state


def apply_http_metrics_finalize(
    *,
    request: Request,
    root_attrs: RootSpanAttributes,
    elapsed: float,
) -> None:
    """
    Record final request metrics while preserving current inflight semantics.

    Called at the end of request handling to:
    1. Rebalance inflight counter if route/account_id changed during request
    2. Record the request duration and status code
    3. Decrement the inflight counter

    Args:
        request: The incoming HTTP request.
        root_attrs: The root span attributes.
        elapsed: The request duration in seconds.
    """
    start_state = getattr(request.state, "http_metrics_start_state", None)
    initial_route = getattr(start_state, "route", root_attrs.http_route)
    initial_account_id = getattr(start_state, "account_id", root_attrs.account_id)
    final_route = _get_route_template(request)
    final_account_id = root_attrs.account_id

    # Rebalance inflight counter if route/account_id changed during request
    if (final_route, final_account_id) != (initial_route, initial_account_id):
        try:
            HttpRequestLifecycleDataSource.set_inflight(
                route=initial_route,
                value=_inflight_delta(initial_route, initial_account_id, -1),
                account_id=initial_account_id,
            )
            HttpRequestLifecycleDataSource.set_inflight(
                route=final_route,
                value=_inflight_delta(final_route, final_account_id, +1),
                account_id=final_account_id,
            )

        except (TypeError, ValueError, AttributeError) as e:
            _log_metrics_failure(
                "http.inflight rebalance failed",
                route=final_route,
                account_id=final_account_id,
                error=e,
            )

        except Exception as e:
            logger.error(
                "Unexpected error in http.inflight rebalance: %s",
                str(e),
                extra={
                    "route": final_route,
                    "account_id": final_account_id,
                },
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    # Record request duration and status code
    try:
        HttpRequestLifecycleDataSource.record_request(
            method=root_attrs.http_method,
            route=final_route,
            status=str(root_attrs.http_status_code or 500),
            duration_seconds=elapsed,
            account_id=final_account_id,
        )

    except (TypeError, ValueError, AttributeError) as e:
        _log_metrics_failure(
            "http.request recording failed",
            route=final_route,
            account_id=final_account_id,
            error=e,
        )

    except Exception as e:
        logger.error(
            "Unexpected error in http.request recording: %s",
            str(e),
            extra={
                "route": final_route,
                "account_id": final_account_id,
            },
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )

    # Decrement inflight counter
    try:
        HttpRequestLifecycleDataSource.set_inflight(
            route=final_route,
            value=_inflight_delta(final_route, final_account_id, -1),
            account_id=final_account_id,
        )

    except (TypeError, ValueError, AttributeError) as e:
        _log_metrics_failure(
            "http.inflight decrement failed",
            route=final_route,
            account_id=final_account_id,
            error=e,
        )

    except Exception as e:
        logger.error(
            "Unexpected error in http.inflight decrement: %s",
            str(e),
            extra={
                "route": final_route,
                "account_id": final_account_id,
            },
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )


async def _execute_request_with_span(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    root_attrs: RootSpanAttributes,
    span_cm: Optional[Any],
) -> Response:
    """
    Execute the request with optional span context management.

    This function encapsulates the common logic for both cases:
    1. When OTel is available and a span context manager exists
    2. When OTel is not available or span creation failed

    Uses `contextlib.nullcontext` to unify the handling of both cases,
    eliminating code duplication.

    Args:
        request: The incoming HTTP request.
        call_next: The next middleware/handler in the chain.
        root_attrs: The root span attributes.
        span_cm: The span context manager (if available), or None.

    Returns:
        The response from the next handler.

    Raises:
        Any exception raised by the next handler.
    """
    # Use nullcontext when span_cm is None to unify handling
    context = span_cm if span_cm is not None else nullcontext()

    with context as span:
        try:
            response = await call_next(request)

            # Update route and span name if we have a real span
            if span is not None:
                _maybe_update_route_and_span_name(
                    request=request,
                    root_attrs=root_attrs,
                    span=span,
                )
                # Apply root attributes to span
                maybe_apply_root_span_attributes(root_attrs)

            # Set status code from response
            root_attrs.http_status_code = int(getattr(response, "status_code", 500))

            # Apply response state (status code, etc.)
            maybe_apply_root_span_response(root_attrs)

            return response

        except Exception as exc:
            # Update route and span name if we have a real span
            if span is not None:
                _maybe_update_route_and_span_name(
                    request=request,
                    root_attrs=root_attrs,
                    span=span,
                )
                # Apply root attributes to span
                maybe_apply_root_span_attributes(root_attrs)

            # Apply error state to span
            maybe_apply_root_span_error(root_attrs, exc)

            raise


def create_http_observability_middleware() -> Callable[[Request, Callable], Response]:
    """
    Create the unified HTTP observability middleware.

    This middleware handles:
    - HTTP metrics collection (inflight, request duration, status codes)
    - OTel trace span creation and management
    - Context propagation (root observability context binding)

    Returns:
        The middleware function.
    """

    async def middleware(request: Request, call_next: Callable) -> Response:
        # Skip metrics/tracing for health check and metrics endpoints
        if should_skip_http_metrics(request):
            return await call_next(request)

        # Extract request information
        raw_path = str(request.url.path)
        route_template = _get_route_template(request)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

        # Create root span attributes
        root_attrs = create_root_span_attributes(
            http_method=request.method,
            http_route=route_template,
            request_id=request_id,
            url_path=raw_path,
            url_scheme=request.url.scheme,
            http_host=request.url.netloc,
            source_type=request.headers.get("x-source-type"),
            source_version=request.headers.get("x-source-version"),
        )

        # Attach to request state for later access
        request.state.root_span_attrs = root_attrs
        request.state.request_id = request_id

        # Bind root context and start metrics
        root_token = bind_root_observability_context(root_attrs)
        apply_http_metrics_start(request=request, root_attrs=root_attrs)

        start = time.perf_counter()

        try:
            # Try to create a root span if OTel is available
            span_cm = maybe_start_root_span(request, root_attrs)

            # Execute the request with unified span handling
            response = await _execute_request_with_span(
                request=request,
                call_next=call_next,
                root_attrs=root_attrs,
                span_cm=span_cm,
            )

            return response

        finally:
            # Calculate elapsed time and finalize metrics
            elapsed = time.perf_counter() - start
            apply_http_metrics_finalize(
                request=request,
                root_attrs=root_attrs,
                elapsed=elapsed,
            )

            # Reset root context
            reset_root_observability_context(root_token)

    return middleware
