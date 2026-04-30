# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenTelemetry tracer integration for OpenViking."""

import functools
import inspect
import json
import logging
from typing import Any, Callable, Optional

from loguru import logger

# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.context import Context
    from opentelemetry.propagate import extract, inject
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import Status, StatusCode, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as OTLPGrpcSpanExporter,
        )
    except ImportError:
        OTLPGrpcSpanExporter = None

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as OTLPHttpSpanExporter,
        )
    except ImportError:
        OTLPHttpSpanExporter = None
except ImportError:
    otel_trace = None
    TracerProvider = None
    Status = None
    StatusCode = None
    BatchSpanProcessor = None
    OTLPGrpcSpanExporter = None
    OTLPHttpSpanExporter = None
    TraceContextTextMapPropagator = None
    Context = None
    extract = None
    inject = None
    Resource = None


# Global tracer instance
_otel_tracer: Any = None
_propagator: Any = None
_trace_id_filter_added: bool = False


def _log_trace_internal_failure(message: str) -> None:
    logger.debug(message, exc_info=True)


class TraceIdLoggingFilter(logging.Filter):
    """日志过滤器：注入 TraceID"""

    def filter(self, record):
        record.trace_id = get_trace_id()
        return True


def _setup_logging():
    """Setup logging with trace_id injection."""
    global _trace_id_filter_added

    if _trace_id_filter_added:
        return

    try:
        # Configure logger to patch records with trace_id
        logger.configure(
            patcher=lambda record: record.__setitem__(
                "extra", {**record["extra"], "trace_id": get_trace_id()}
            )
        )
        _trace_id_filter_added = True
    except Exception:
        _log_trace_internal_failure("[TRACER] failed to configure loguru trace_id patcher")

    # Also setup standard logging filter
    try:
        standard_logger = logging.getLogger()
        for handler in standard_logger.handlers:
            if not any(isinstance(f, TraceIdLoggingFilter) for f in handler.filters):
                handler.addFilter(TraceIdLoggingFilter())
    except Exception:
        _log_trace_internal_failure("[TRACER] failed to attach standard logging trace_id filter")


def init_tracer_from_server_config(server_config: Any) -> Any:
    """Initialize tracer from server.observability.traces config.

    Args:
        server_config: The server configuration containing observability settings.

    Returns:
        The initialized tracer, or None if initialization failed or disabled.
    """
    try:
        trace_cfg = server_config.observability.traces
        if not trace_cfg.enabled:
            logger.info("[TRACER] disabled in server.observability.traces")
            return None

        if not trace_cfg.endpoint:
            logger.warning("[TRACER] server.observability.traces.endpoint not configured")
            return None

        return init_tracer(
            endpoint=trace_cfg.endpoint,
            service_name=trace_cfg.service_name,
            protocol=trace_cfg.protocol,
            insecure=trace_cfg.tls.insecure,
            headers=trace_cfg.headers,
            enabled=trace_cfg.enabled,
        )
    except Exception as e:
        logger.warning(f"[TRACER] init from server config failed: {e}")
        return None


def _init_asyncio_instrumentation() -> None:
    """Initialize asyncio instrumentation to create child spans for create_task."""
    try:
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor

        AsyncioInstrumentor().instrument()
        logger.info("[TRACER] initialized AsyncioInstrumentor")
    except ImportError:
        logger.warning("[TRACER] opentelemetry-instrumentation-asyncio not installed")
    except Exception as e:
        logger.warning(f"[TRACER] failed to init AsyncioInstrumentor: {e}")


def init_tracer(
    endpoint: str,
    service_name: str,
    protocol: str = "grpc",
    insecure: bool = False,
    headers: Optional[dict[str, str]] = None,
    enabled: bool = True,
) -> Any:
    """Initialize the OpenTelemetry tracer.

    Args:
        endpoint: OTLP endpoint
        service_name: Service name for tracing
        protocol: OTLP protocol ("grpc" or "http")
        insecure: For OTLP/gRPC only. When True, use plaintext instead of TLS.
        headers: Additional OTLP exporter headers for vendor-specific auth.
        enabled: Whether to enable tracing

    Returns:
        The initialized tracer, or None if initialization failed
    """
    global _otel_tracer, _propagator

    if not enabled:
        logger.info("[TRACER] disabled by config")
        return None

    if otel_trace is None or TracerProvider is None or Resource is None:
        logger.warning(
            "OpenTelemetry not installed. Install with: uv pip install opentelemetry-api "
            "opentelemetry-sdk opentelemetry-exporter-otlpprotogrpc"
        )
        return None

    try:
        normalized_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        resource_attributes = {
            "service.name": service_name,
        }
        resource = Resource.create(resource_attributes)

        protocol = protocol.lower()
        if protocol == "grpc":
            if OTLPGrpcSpanExporter is None:
                raise ImportError("gRPC OTLP trace exporter not available")
            try:
                trace_exporter = OTLPGrpcSpanExporter(
                    endpoint=endpoint,
                    insecure=insecure,
                    headers=normalized_headers,
                )
            except TypeError:
                trace_exporter = OTLPGrpcSpanExporter(
                    endpoint=endpoint,
                    headers=normalized_headers,
                )
        elif protocol == "http":
            if OTLPHttpSpanExporter is None:
                raise ImportError("HTTP OTLP trace exporter not available")
            if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
                raise ValueError(
                    "OTLP/HTTP trace endpoint must include scheme, e.g. 'http://localhost:4318/v1/traces'"
                )
            trace_exporter = OTLPHttpSpanExporter(
                endpoint=endpoint,
                headers=normalized_headers,
            )
        else:
            raise ValueError(f"Unsupported trace protocol: {protocol}")

        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(
            BatchSpanProcessor(
                trace_exporter,
                max_export_batch_size=100,
                schedule_delay_millis=1000,
                export_timeout_millis=60000,
            )
        )
        otel_trace.set_tracer_provider(trace_provider)

        _otel_tracer = otel_trace.get_tracer(service_name)
        _propagator = TraceContextTextMapPropagator()

        # Setup logging with trace_id
        _setup_logging()

        # Initialize asyncio instrumentation to create child spans for create_task
        _init_asyncio_instrumentation()

        logger.info(
            "[TRACER] initialized with service_name=%s, protocol=%s, endpoint=%s",
            service_name,
            protocol,
            endpoint,
        )
        return _otel_tracer

    except Exception as e:
        logger.warning(f"[TRACER] initialized failed: {type(e).__name__}: {e}")
        return None


def get_tracer() -> Any:
    """Get the current tracer instance."""
    return _otel_tracer


def is_enabled() -> bool:
    """Check if tracer is enabled."""
    return _otel_tracer is not None


def get_trace_id() -> str:
    """Get the current trace ID as a hex string.

    Returns:
        The trace ID in hex format, or empty string if no active span
    """
    if _otel_tracer is None:
        return ""

    try:
        current_span = otel_trace.get_current_span()
        if current_span is not None and hasattr(current_span, "context"):
            trace_id = "{:032x}".format(current_span.context.trace_id)
            return trace_id
    except Exception:
        _log_trace_internal_failure("[TRACER] failed to resolve current trace id")
    return ""


def to_trace_info() -> str:
    """Inject current trace context into a JSON string.

    Returns:
        JSON string with trace context, or empty JSON object if no active span
    """
    if _otel_tracer is None:
        return "{}"

    carrier = {}
    inject(carrier)
    return json.dumps(carrier)


def from_trace_info(trace_info: str) -> Optional[Any]:
    """Extract trace context from a JSON string.

    Args:
        trace_info: JSON string with trace context

    Returns:
        The extracted context, or None if extraction failed
    """
    if _otel_tracer is None or not trace_info:
        return None

    try:
        carrier = json.loads(trace_info)
        context = extract(carrier)
        return context
    except Exception as e:
        logger.debug(f"[TRACER] failed to extract trace context: {e}")
        return None


def start_span(
    name: str,
    trace_id: Optional[str] = None,
) -> Any:
    """Start a new span.

    Args:
        name: Span name
        trace_id: Optional trace ID to continue from

    Returns:
        A context manager for the span
    """
    return tracer.start_as_current_span(name=name, trace_id=trace_id)


def set_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current span."""
    tracer.set(key, value)


def add_event(name: str) -> None:
    """Add an event to the current span."""
    tracer.info(name)


def record_exception(exception: Exception) -> None:
    """Record an exception on the current span."""
    tracer.error(str(exception), e=exception, console=False)


class tracer:
    """Decorator class for tracing functions.

    Usage:
        @tracer("my_function")
        async def my_function():
            ...

        @tracer("my_function", ignore_result=False)
        def sync_function():
            ...

        @tracer("new_trace", is_new_trace=True)
        def new_trace_function():
            ...
    """

    def __init__(
        self,
        name: Optional[str] = None,
        ignore_result: bool = True,
        ignore_args: bool = True,
        is_new_trace: bool = False,
    ):
        """Initialize the tracer decorator.

        Args:
            name: Custom name for the span (defaults to function name)
            ignore_result: Whether to ignore the function result in the span
            ignore_args: Whether to ignore function arguments, or list of arg names to include
            is_new_trace: Whether to create a new trace (vs continue existing)
        """
        # 忽略结果
        self.ignore_result = ignore_result
        self.ignore_args = ignore_args

        # 需要忽略的参数
        if ignore_args is True:
            self.arg_trace_checker = lambda name: False
        elif ignore_args is False:
            self.arg_trace_checker = lambda name: True
        else:
            self.arg_trace_checker = lambda name: name not in ignore_args

        self.name = name
        self.is_new_trace = is_new_trace

    def __call__(self, func: Callable) -> Callable:
        """Decorator to trace a function."""
        context = Context() if self.is_new_trace else None

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if _otel_tracer is None:
                    return await func(*args, **kwargs)

                span_name = self.name or f"{func.__module__}.{func.__name__}"
                with self.start_as_current_span(name=span_name, context=context) as span:
                    try:
                        # 记录输入参数
                        if not self.ignore_args and args:
                            self.info("func_args", str(args))
                        func_kwargs = {k: v for k, v in kwargs.items() if self.arg_trace_checker(k)}
                        if len(func_kwargs) > 0:
                            self.info("func_kwargs", str(func_kwargs))

                        result = await func(*args, **kwargs)

                        if result is not None and not self.ignore_result:
                            self.info(f"result: {result}")

                        return result
                    except Exception as e:
                        span.record_exception(exception=e)
                        span.set_status(Status(StatusCode.ERROR))
                        raise

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                if _otel_tracer is None:
                    return func(*args, **kwargs)

                span_name = self.name or f"{func.__module__}.{func.__name__}"
                with self.start_as_current_span(name=span_name, context=context) as span:
                    try:
                        # 记录输入参数
                        if not self.ignore_args and args:
                            self.set("func_args", str(args))
                        func_kwargs = {k: v for k, v in kwargs.items() if self.arg_trace_checker(k)}
                        if len(func_kwargs) > 0:
                            self.set("func_kwargs", str(func_kwargs))

                        result = func(*args, **kwargs)

                        if result is not None and not self.ignore_result:
                            self.info(f"result: {result}")

                        return result
                    except Exception as e:
                        span.record_exception(exception=e)
                        span.set_status(Status(StatusCode.ERROR))
                        raise

            return sync_wrapper

    @classmethod
    def start_as_current_span(cls, name: str, context=None, trace_id=None):
        """Start a new span as current context."""
        if _otel_tracer is None:
            return _DummySpanContext()

        try:
            if trace_id is not None:
                carrier = {"traceparent": f"00-{trace_id}-{format(1, '016x')}-01"}
                input_context = extract(carrier=carrier)
            elif context is not None:
                input_context = context
            else:
                input_context = None

            return _otel_tracer.start_as_current_span(name=name, context=input_context)
        except Exception as e:
            logger.debug(f"[TRACER] failed to start span: {e}")
            return _DummySpanContext()

    @staticmethod
    def get_trace_id() -> str:
        """Get the current trace ID as a hex string."""
        if _otel_tracer is None:
            return ""

        try:
            current_span = otel_trace.get_current_span()
            if current_span is not None and hasattr(current_span, "context"):
                trace_id = "{:032x}".format(current_span.context.trace_id)
                return trace_id
        except Exception:
            _log_trace_internal_failure("[TRACER] failed to resolve decorator trace id")
        return ""

    @staticmethod
    def is_enabled() -> bool:
        """Check if tracer is enabled."""
        return _otel_tracer is not None

    @staticmethod
    def set(key: str, value: Any) -> None:
        """Set an attribute on the current span."""
        if _otel_tracer is None:
            return

        try:
            current_span = otel_trace.get_current_span()
            if current_span:
                # 检查 span 是否已结束
                if hasattr(current_span, "end_time") and current_span.end_time:
                    return  # span 已结束，不设置 attribute
                current_span.set_attribute(key, str(value))
        except Exception:
            _log_trace_internal_failure(f"[TRACER] failed to set span attribute key={key}")

    @staticmethod
    def info(line: str, console: bool = False) -> None:
        """Add an event to the current span."""
        if _otel_tracer is None:
            return

        try:
            current_span = otel_trace.get_current_span()
            if current_span:
                # 检查 span 是否已结束
                if hasattr(current_span, "end_time") and current_span.end_time:
                    return  # span 已结束，不添加 event
                current_span.add_event(line)
        except Exception:
            import traceback

            traceback.print_stack()

    @staticmethod
    def info_span(line: str, console: bool = False) -> None:
        """Create a new span with the given name."""
        if console:
            logger.info(line)
        if _otel_tracer is None:
            return
        with tracer.start_as_current_span(name=line):
            pass

    @staticmethod
    def error(line: str, e: Optional[Exception] = None, console: bool = True) -> None:
        """Record an error on the current span."""
        if _otel_tracer is None:
            return

        try:
            current_span = otel_trace.get_current_span()
            if current_span:
                # 检查 span 是否已结束
                if hasattr(current_span, "end_time") and current_span.end_time:
                    return  # span 已结束，不记录 error
                if e is not None:
                    current_span.set_status(Status(StatusCode.ERROR))
                    current_span.record_exception(exception=e, attributes={"error": line})
                else:
                    current_span.set_status(Status(StatusCode.ERROR))
                    current_span.add_event(line)
        except Exception:
            _log_trace_internal_failure("[TRACER] failed to record span error")


class _DummySpanContext:
    """Dummy context manager for when tracer is not enabled."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __aenter__(self):
        return self

    def __aexit__(self, *args):
        pass

    def set_attribute(self, key: str, value: Any):
        pass

    def add_event(self, name: str):
        pass

    def record_exception(self, exception: Exception):
        pass

    def set_status(self, status: Any):
        pass


# Keep trace_func as alias for backwards compatibility
trace_func = tracer


def trace(name: str):
    """Simple decorator to trace a function with a given name.

    Usage:
        @tracer.trace("my_function")
        async def my_function():
            ...
    """
    return tracer(name=name)
