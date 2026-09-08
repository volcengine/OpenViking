# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenTelemetry tracer integration for OpenViking."""

import functools
import inspect
import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.context import Context
    from opentelemetry.propagate import extract, inject
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import Status, StatusCode, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    except ImportError:
        MessageToDict = None
        encode_spans = None

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
    SpanExporter = None
    SpanExportResult = None
    OTLPGrpcSpanExporter = None
    OTLPHttpSpanExporter = None
    TraceContextTextMapPropagator = None
    Context = None
    extract = None
    inject = None
    Resource = None
    MessageToDict = None
    encode_spans = None


# Global tracer instance
_otel_tracer: Any = None
_propagator: Any = None
_trace_id_filter_added: bool = False
_trace_capture_content: bool = False
_trace_content_max_length: int = 4096

_TRACE_CONTENT_MAX_LENGTH_LIMIT = 65_536
_TRACE_CONTENT_TRUNCATED_SUFFIX = "...[truncated]"
_TRACE_SENSITIVE_KEY = (
    r"[a-z0-9_.-]*(?:api[_-]?key|app[_-]?key|authorization|access[_-]?token|"
    r"refresh[_-]?token|token|secret|password)"
)
_TRACE_SENSITIVE_QUOTED_VALUE_RE = re.compile(
    rf"""(?ix)
    (?P<prefix>
        ["']?{_TRACE_SENSITIVE_KEY}["']?\s*[:=]\s*["']
    )
    (?P<value>[^"']*)
    (?P<suffix>["'])
    """
)
_TRACE_SENSITIVE_UNQUOTED_VALUE_RE = re.compile(
    rf"(?i)(\b{_TRACE_SENSITIVE_KEY}\b\s*[:=]\s*)((?:Bearer\s+)?[^\s,}}\]]+)"
)
_TRACE_BEARER_TOKEN_RE = re.compile(r"""(?i)(\bBearer\s+)([^\s,}\]'\"]+)""")
_TRACE_IMAGE_DATA_URL_RE = re.compile(r"(?i)(data:image/[^,\s'\"]+;base64,)([a-z0-9+/=_-]+)")


def _log_trace_internal_failure(message: str) -> None:
    logger.debug(message, exc_info=True)


def _validate_trace_content_max_length(content_max_length: int) -> int:
    if not 1 <= content_max_length <= _TRACE_CONTENT_MAX_LENGTH_LIMIT:
        raise ValueError(
            f"content_max_length must be between 1 and {_TRACE_CONTENT_MAX_LENGTH_LIMIT}"
        )
    return content_max_length


def _configure_trace_content(capture_content: bool, content_max_length: int) -> None:
    global _trace_capture_content, _trace_content_max_length

    _trace_capture_content = bool(capture_content)
    _trace_content_max_length = _validate_trace_content_max_length(content_max_length)


def _redact_trace_content(value: str) -> str:
    value = _TRACE_IMAGE_DATA_URL_RE.sub(r"\1[redacted]", value)
    value = _TRACE_SENSITIVE_QUOTED_VALUE_RE.sub(
        lambda match: f"{match.group('prefix')}[redacted]{match.group('suffix')}",
        value,
    )
    value = _TRACE_SENSITIVE_UNQUOTED_VALUE_RE.sub(r"\1[redacted]", value)
    return _TRACE_BEARER_TOKEN_RE.sub(r"\1[redacted]", value)


def _truncate_trace_content(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= len(_TRACE_CONTENT_TRUNCATED_SUFFIX):
        return _TRACE_CONTENT_TRUNCATED_SUFFIX[:max_length]
    return value[: max_length - len(_TRACE_CONTENT_TRUNCATED_SUFFIX)] + (
        _TRACE_CONTENT_TRUNCATED_SUFFIX
    )


def _prepare_trace_content(value: Any) -> str:
    return _truncate_trace_content(
        _redact_trace_content(str(value)),
        _trace_content_max_length,
    )


def _source_attributes(frame: Any) -> dict[str, Any]:
    if frame is None:
        return {}
    namespace = str(frame.f_globals.get("__name__", "unknown"))
    function_name = getattr(frame.f_code, "co_qualname", frame.f_code.co_name)
    return {
        "code.namespace": namespace,
        "code.function.name": f"{namespace}.{function_name}",
        "code.line.number": frame.f_lineno,
    }


_SpanExporterBase = SpanExporter if SpanExporter is not None else object


class LocalJsonlSpanExporter(_SpanExporterBase):
    """OpenTelemetry span exporter that writes OTLP JSON batches to a local JSONL file.

    Each exported batch is encoded as one JSON line using the protobuf JSON
    representation of ``ExportTraceServiceRequest``. The file is rotated by
    size and is intended for offline support/debug upload.
    """

    def __init__(
        self,
        path: str,
        *,
        rotation_mb: int = 40,
        backup_count: int = 2,
    ) -> None:
        if (
            SpanExporter is None
            or SpanExportResult is None
            or MessageToDict is None
            or encode_spans is None
        ):
            raise ImportError("OpenTelemetry trace exporter dependencies are not available")
        super().__init__()
        self._path = Path(os.path.expandvars(os.path.expanduser(path)))
        self._max_bytes = int(rotation_mb) * 1024 * 1024
        self._backup_count = int(backup_count)
        self._lock = threading.RLock()
        self._shutdown = False

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Validate writability during initialization so configuration errors
        # are surfaced once and tracing can fail open.
        with self._path.open("a", encoding="utf-8"):
            pass

    def export(self, spans: Any) -> Any:
        if self._shutdown:
            return SpanExportResult.FAILURE
        if not spans:
            return SpanExportResult.SUCCESS

        try:
            request = encode_spans(spans)
            payload = MessageToDict(
                request,
                preserving_proto_field_name=False,
                always_print_fields_with_no_presence=False,
            )
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded_size = len(line.encode("utf-8"))
            with self._lock:
                self._rotate_if_needed(encoded_size)
                with self._path.open("a", encoding="utf-8") as fp:
                    fp.write(line)
            return SpanExportResult.SUCCESS
        except Exception:
            _log_trace_internal_failure("[TRACER] failed to export local JSONL spans")
            return SpanExportResult.FAILURE

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        self._shutdown = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self._max_bytes <= 0 or not self._path.exists():
            return
        try:
            if self._path.stat().st_size + incoming_bytes <= self._max_bytes:
                return
        except OSError:
            return

        if self._backup_count <= 0:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            return

        for index in range(self._backup_count, 0, -1):
            src = self._path.with_name(f"{self._path.name}.{index}")
            if index == self._backup_count:
                try:
                    src.unlink()
                except FileNotFoundError:
                    pass
                continue
            dst = self._path.with_name(f"{self._path.name}.{index + 1}")
            try:
                src.replace(dst)
            except FileNotFoundError:
                pass

        try:
            self._path.replace(self._path.with_name(f"{self._path.name}.1"))
        except FileNotFoundError:
            pass


class TraceIdLoggingFilter(logging.Filter):
    """日志过滤器：注入 TraceID"""

    def filter(self, record):
        trace_id = get_trace_id()
        record.trace_id = trace_id
        if trace_id:
            record.msg = f"[{trace_id}] {record.msg}"
        return True


def _setup_logging():
    """Setup logging with trace_id injection."""
    global _trace_id_filter_added

    if _trace_id_filter_added:
        return

    try:
        # Configure logger to patch records with trace_id
        def _patch_trace_id(record):
            trace_id = get_trace_id()
            record["extra"]["trace_id"] = trace_id
            if trace_id:
                record["message"] = f"[{trace_id}] {record['message']}"

        logger.configure(patcher=_patch_trace_id)
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
            _configure_trace_content(False, 4096)
            logger.info("[TRACER] disabled in server.observability.traces")
            return None

        if trace_cfg.protocol.lower() != "local" and not trace_cfg.endpoint:
            _configure_trace_content(False, 4096)
            logger.warning("[TRACER] server.observability.traces.endpoint not configured")
            return None

        return init_tracer(
            endpoint=trace_cfg.endpoint,
            service_name=trace_cfg.service_name,
            protocol=trace_cfg.protocol,
            insecure=trace_cfg.tls.insecure,
            headers=trace_cfg.headers,
            enabled=trace_cfg.enabled,
            local_path=trace_cfg.local_path,
            local_rotation_mb=trace_cfg.local_rotation_mb,
            local_backup_count=trace_cfg.local_backup_count,
            capture_content=getattr(trace_cfg, "capture_content", False),
            content_max_length=getattr(trace_cfg, "content_max_length", 4096),
        )
    except Exception as e:
        _configure_trace_content(False, 4096)
        logger.warning(f"[TRACER] init from server config failed: {e}")
        return None


def _init_asyncio_instrumentation() -> None:
    """Initialize asyncio instrumentation to create child spans for create_task."""
    try:
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor

        AsyncioInstrumentor().instrument()
        logger.debug("[TRACER] initialized AsyncioInstrumentor")
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
    local_path: str = "~/.openviking/logs/traces.jsonl",
    local_rotation_mb: int = 40,
    local_backup_count: int = 2,
    capture_content: bool = False,
    content_max_length: int = 4096,
) -> Any:
    """Initialize the OpenTelemetry tracer.

    Args:
        endpoint: OTLP endpoint
        service_name: Service name for tracing
        protocol: OTLP protocol ("grpc" or "http")
        insecure: For OTLP/gRPC only. When True, use plaintext instead of TLS.
        headers: Additional OTLP exporter headers for vendor-specific auth.
        enabled: Whether to enable tracing
        local_path: JSONL file path when protocol is "local".
        local_rotation_mb: Maximum size in MB before rotating local JSONL file.
        local_backup_count: Number of rotated local JSONL files to keep.
        capture_content: Whether diagnostic message content may be exported.
        content_max_length: Maximum characters exported for one diagnostic message.

    Returns:
        The initialized tracer, or None if initialization failed
    """
    global _otel_tracer, _propagator

    if not enabled:
        _configure_trace_content(False, 4096)
        logger.info("[TRACER] disabled by config")
        return None

    if otel_trace is None or TracerProvider is None or Resource is None:
        _configure_trace_content(False, 4096)
        logger.warning(
            "OpenTelemetry not installed. Install with: uv pip install opentelemetry-api "
            "opentelemetry-sdk opentelemetry-exporter-otlpprotogrpc"
        )
        return None

    try:
        validated_content_max_length = _validate_trace_content_max_length(content_max_length)
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
        elif protocol == "local":
            trace_exporter = LocalJsonlSpanExporter(
                local_path,
                rotation_mb=local_rotation_mb,
                backup_count=local_backup_count,
            )
        else:
            raise ValueError(f"Unsupported trace protocol: {protocol}")

        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(
            BatchSpanProcessor(
                trace_exporter,
                max_export_batch_size=50,
                schedule_delay_millis=1000,
                export_timeout_millis=60000,
            )
        )
        otel_trace.set_tracer_provider(trace_provider)

        _otel_tracer = otel_trace.get_tracer(service_name)
        _propagator = TraceContextTextMapPropagator()
        _configure_trace_content(capture_content, validated_content_max_length)

        # Setup logging with trace_id
        _setup_logging()

        # Initialize asyncio instrumentation to create child spans for create_task
        _init_asyncio_instrumentation()

        logger.debug(
            "[TRACER] initialized with service_name=%s, protocol=%s, endpoint=%s",
            service_name,
            protocol,
            endpoint,
        )
        return _otel_tracer

    except Exception as e:
        _configure_trace_content(False, 4096)
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


@contextmanager
def start_current_span(name: str, *, trace_id: Optional[str] = None):
    """Start a span as the current context for an explicit code block."""

    with tracer.start_as_current_span(name=name, trace_id=trace_id) as span:
        yield span


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


def add_event(name: str, attributes: Optional[Mapping[str, Any]] = None) -> None:
    """Add a named event with optional typed attributes to the current span."""
    tracer.add_event(name, attributes)


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
                        if _trace_capture_content and not self.ignore_args and args:
                            self.set("func_args", _prepare_trace_content(args))
                        func_kwargs = {k: v for k, v in kwargs.items() if self.arg_trace_checker(k)}
                        if _trace_capture_content and func_kwargs:
                            self.set("func_kwargs", _prepare_trace_content(func_kwargs))

                        result = await func(*args, **kwargs)

                        if result is not None and not self.ignore_result:
                            self.info(f"result: {result}")

                        return result
                    except Exception as e:
                        self.error("e", e=e)
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
                        if _trace_capture_content and not self.ignore_args and args:
                            self.set("func_args", _prepare_trace_content(args))
                        func_kwargs = {k: v for k, v in kwargs.items() if self.arg_trace_checker(k)}
                        if _trace_capture_content and func_kwargs:
                            self.set("func_kwargs", _prepare_trace_content(func_kwargs))

                        result = func(*args, **kwargs)

                        if result is not None and not self.ignore_result:
                            self.info(f"result: {result}")

                        return result
                    except Exception as e:
                        self.error("e", e=e)
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
        return get_trace_id()

    @staticmethod
    def is_enabled() -> bool:
        """Check if tracer is enabled."""
        return is_enabled()

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
    def add_event(name: str, attributes: Optional[Mapping[str, Any]] = None) -> None:
        """Add a named event while preserving supported attribute value types."""
        if _otel_tracer is None:
            return

        try:
            current_span = otel_trace.get_current_span()
            if current_span:
                if hasattr(current_span, "end_time") and current_span.end_time:
                    return
                if attributes is None:
                    current_span.add_event(name)
                else:
                    current_span.add_event(name, attributes=dict(attributes))
        except Exception:
            _log_trace_internal_failure(f"[TRACER] failed to add span event name={name}")

    @staticmethod
    def info(line: str, console: bool = False) -> None:
        """Record a diagnostic under a stable event name.

        Diagnostic text is treated as potentially sensitive and is exported only
        when trace content capture is explicitly enabled.
        """
        if console:
            logger.opt(depth=1).info(line)
        if _otel_tracer is None:
            return

        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        try:
            attributes = _source_attributes(caller)
            if _trace_capture_content:
                attributes["openviking.log.message"] = _prepare_trace_content(line)
            tracer.add_event("openviking.log", attributes)
        except Exception:
            _log_trace_internal_failure("[TRACER] failed to record diagnostic event")
        finally:
            del frame

    @staticmethod
    def info_span(line: str, console: bool = False) -> None:
        """Create a new span with the given name."""
        if console:
            logger.opt(depth=1).info(line)
        if _otel_tracer is None:
            return
        with tracer.start_as_current_span(name=line):
            pass

    @staticmethod
    def error(line: str, e: Optional[Exception] = None, console: bool = True) -> None:
        """Record an error on the current span."""
        if console:
            if e is not None:
                logger.opt(depth=1).exception(f"{line}", exc_info=e)
            else:
                logger.opt(depth=1).error(line)
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

    def add_event(self, name: str, attributes: Optional[Mapping[str, Any]] = None):
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
