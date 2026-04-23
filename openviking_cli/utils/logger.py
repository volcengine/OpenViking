# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Logging utilities for OpenViking.
"""

import logging
import sys
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Tuple
from uuid import uuid4

from openviking.observability.context import (
    bind_execution_context,
    get_observability_context,
)

# Try to import opentelemetry - will be None if not installed
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import Status, StatusCode, format_span_id, format_trace_id

    # Try to import the gRPC exporter.
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter as OTLPGrpcLogExporter,
        )
    except ImportError:
        OTLPGrpcLogExporter = None

    # Try to import the HTTP exporter.
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter as OTLPHttpLogExporter,
        )
    except ImportError:
        OTLPHttpLogExporter = None
except ImportError:
    otel_trace = None
    set_logger_provider = None
    LoggerProvider = None
    LoggingHandler = None
    BatchLogRecordProcessor = None
    OTLPGrpcLogExporter = None
    OTLPHttpLogExporter = None
    Resource = None
    Status = None
    StatusCode = None
    format_span_id = None
    format_trace_id = None


# Global OTel log handler state
_otel_log_handler_initialized = False
_otel_log_handler: Any = None


def _get_log_context() -> dict[str, Any]:
    """
    Build the log context dynamically for the current context.

    This function builds the log context from scratch on each call, ensuring
    that any context changes (e.g., account_id updated after authentication)
    are immediately reflected in subsequent log records.

    Context sources (in priority order):
    1. OTel current span (for trace_id, span_id)
    2. Root observability context (for request_id, account_id, etc.)
    3. Operation observability context (for operation, telemetry_id, etc.)
    4. Execution-level trace context (fallback when no OTel span)

    Returns:
        A dictionary containing all log context fields.
    """
    context: dict[str, Any] = {
        "trace_id": "",
        "span_id": "",
        "request_id": "",
        "operation": "",
        "account_id": "",
        "user_id": "",
        "agent_id": "",
    }

    # 1. Get trace_id and span_id from OTel span if available
    if otel_trace is not None and format_trace_id is not None and format_span_id is not None:
        try:
            current_span = otel_trace.get_current_span()
            if current_span is not None and hasattr(current_span, "context"):
                span_context = current_span.get_span_context()
                if span_context.is_valid:
                    context["trace_id"] = format_trace_id(span_context.trace_id)
                    context["span_id"] = format_span_id(span_context.span_id)
        except Exception:
            # Best-effort only - don't fail if OTel context retrieval fails
            pass

    # 2. Get unified observability context (contains root and operation)
    obs_ctx = get_observability_context()

    # Get fields from root context
    if obs_ctx.root is not None:
        root_fields = obs_ctx.root.to_log_fields()
        for key, value in root_fields.items():
            if value is not None:
                context[key] = value

    # Get fields from operation context
    if obs_ctx.operation is not None:
        operation_fields = obs_ctx.operation.to_log_fields()
        for key, value in operation_fields.items():
            if value is not None:
                context[key] = value

    # 3. Fall back to execution-level trace context if no OTel span
    if not context["trace_id"]:
        context["trace_id"] = obs_ctx.get_trace_id()
    if not context["span_id"]:
        context["span_id"] = obs_ctx.get_span_id()

    return context


def set_log_execution_trace_id(trace_id: Optional[str] = None) -> str:
    """
    Set the execution-level trace ID used by logs outside real OTel spans.

    This function uses the unified ObservabilityContext to store the trace ID.

    Args:
        trace_id: Optional 32-character lowercase hex trace ID. When omitted, a new
            trace ID is generated automatically.

    Returns:
        The effective execution trace ID stored in the current context.
    """
    ctx = get_observability_context()
    effective_trace_id = trace_id or uuid4().hex
    ctx.execution_trace_id = effective_trace_id
    return effective_trace_id


def set_log_execution_span_id(span_id: Optional[str] = None) -> str:
    """
    Set the execution-level span ID used by logs outside real OTel spans.

    This function uses the unified ObservabilityContext to store the span ID.

    Args:
        span_id: Optional 16-character lowercase hex span ID. When omitted, a new
            span ID is generated automatically.

    Returns:
        The effective execution span ID stored in the current context.
    """
    ctx = get_observability_context()
    effective_span_id = span_id or uuid4().hex[:16]
    ctx.execution_span_id = effective_span_id
    return effective_span_id


def get_effective_trace_id() -> str:
    """
    Return the current execution-level trace ID, or an empty string when unset.

    This function uses the unified ObservabilityContext to retrieve the trace ID.
    It also checks the OTel current span for a valid trace ID.

    Returns:
        The effective trace ID, or empty string if not available.
    """
    ctx = get_observability_context()
    return ctx.get_trace_id()


def get_effective_span_id() -> str:
    """
    Return the current execution-level span ID, or an empty string when unset.

    This function uses the unified ObservabilityContext to retrieve the span ID.
    It also checks the OTel current span for a valid span ID.

    Returns:
        The effective span ID, or empty string if not available.
    """
    ctx = get_observability_context()
    return ctx.get_span_id()


def clear_log_execution_trace_id() -> None:
    """
    Clear the execution-level trace ID from the current logging context.

    This function clears the execution trace ID and span ID from the
    unified ObservabilityContext.
    """
    ctx = get_observability_context()
    ctx.execution_trace_id = None
    ctx.execution_span_id = None


@contextmanager
def bind_log_execution_trace(trace_id: Optional[str] = None, span_id: Optional[str] = None):
    """
    Bind a stable execution-level trace ID and span ID for logs emitted in this context.

    This context manager is intended for non-request entry points, such as process startup,
    CLI execution, or background tasks that do not run under a real OTel request span.

    This is a convenience wrapper around the unified `bind_execution_context` from
    the observability context module.

    Args:
        trace_id: Optional 32-character lowercase hex trace ID. When omitted, a new
            trace ID is generated automatically for this execution unit.
        span_id: Optional 16-character lowercase hex span ID. When omitted, the first 16
            characters of the trace_id are used as the span_id.

    Yields:
        A tuple of (trace_id, span_id).
    """
    with bind_execution_context(trace_id, span_id) as (effective_trace_id, effective_span_id):
        yield effective_trace_id, effective_span_id


def init_otel_log_handler(
    protocol: str = "grpc",  # "grpc" or "http"
    endpoint: str = "localhost:4317",  # gRPC default: 4317; HTTP default: 4318
    service_name: str = "openviking-server",
    insecure: bool = False,
    enabled: bool = True,
) -> Any:
    """Initialize OTel LoggingHandler.

    Args:
        protocol: OTLP protocol ("grpc" or "http").
        endpoint: OTLP logs endpoint.
        service_name: Service name.
        insecure: For OTLP/gRPC only. When True, use plaintext instead of TLS.
        enabled: Whether to enable the handler.

    Returns:
        The initialized LoggingHandler instance, or None if initialization fails.
    """
    global _otel_log_handler_initialized, _otel_log_handler

    if not enabled:
        return None

    if _otel_log_handler_initialized:
        return _otel_log_handler

    # Validate protocol
    protocol = protocol.lower()
    if protocol not in ["grpc", "http"]:
        protocol = "grpc"

    if (
        set_logger_provider is None
        or LoggerProvider is None
        or LoggingHandler is None
        or BatchLogRecordProcessor is None
        or Resource is None
    ):
        return None

    try:
        # Create resource
        resource = Resource.create(
            {
                "service.name": service_name,
            }
        )

        # Create logger provider
        logger_provider = LoggerProvider(resource=resource)

        # Select exporter based on protocol
        otlp_exporter = None
        if protocol == "grpc":
            if OTLPGrpcLogExporter is None:
                raise ImportError("gRPC OTLP log exporter not available")
            try:
                otlp_exporter = OTLPGrpcLogExporter(endpoint=endpoint, insecure=insecure)
            except TypeError:
                otlp_exporter = OTLPGrpcLogExporter(endpoint=endpoint)
        elif protocol == "http":
            if OTLPHttpLogExporter is None:
                raise ImportError("HTTP OTLP log exporter not available")
            # HTTP endpoint must be a full URL (with scheme). Do not auto-prefix to avoid implicit behavior.
            if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
                raise ValueError(
                    "OTLP/HTTP endpoint must include scheme, e.g. 'http://localhost:4318/v1/logs'"
                )
            otlp_exporter = OTLPHttpLogExporter(endpoint=endpoint)

        if otlp_exporter is None:
            raise ValueError(f"Failed to create log exporter for protocol={protocol}")

        # Create batch log processor
        log_processor = BatchLogRecordProcessor(otlp_exporter)
        logger_provider.add_log_record_processor(log_processor)

        # Set global logger provider
        set_logger_provider(logger_provider)

        # Create logging handler
        _otel_log_handler = LoggingHandler(
            level=logging.INFO,
            logger_provider=logger_provider,
        )

        _otel_log_handler_initialized = True

        logger = get_logger(__name__)
        logger.info(
            "[OTelLogHandler] initialized with protocol=%s, endpoint=%s, service_name=%s",
            protocol,
            endpoint,
            service_name,
        )

        return _otel_log_handler
    except Exception as e:
        logger = get_logger(__name__)
        logger.warning("[OTelLogHandler] initialization failed: %s", e)
        return None


def attach_otel_log_handler_to_existing_loggers() -> None:
    """Attach the global OTel log handler to already-created loggers."""
    if _otel_log_handler is None:
        return

    add_otel_log_handler_to_logger(logging.getLogger())

    for logger_obj in logging.Logger.manager.loggerDict.values():
        if isinstance(logger_obj, logging.Logger):
            add_otel_log_handler_to_logger(logger_obj)


def init_otel_log_handler_from_server_config(server_config: Any) -> Any:
    """Initialize OTLP log export from server.observability.logs."""
    logs_cfg = server_config.observability.logs
    if not logs_cfg.enabled:
        return None

    handler = init_otel_log_handler(
        protocol=logs_cfg.protocol,
        endpoint=logs_cfg.endpoint,
        service_name=logs_cfg.service_name,
        insecure=logs_cfg.tls.insecure,
        enabled=logs_cfg.enabled,
    )
    attach_otel_log_handler_to_existing_loggers()
    return handler


def add_otel_log_handler_to_logger(logger: logging.Logger) -> None:
    """Add OTel LoggingHandler to the specified logger.

    Args:
        logger: The logger instance to add the handler to.
    """
    if _otel_log_handler is not None:
        # Check if the handler has already been added
        if not any(isinstance(h, type(_otel_log_handler)) for h in logger.handlers):
            logger.addHandler(_otel_log_handler)


class TraceContextFilter(logging.Filter):
    """
    Log filter that injects OTel trace context information.

    Automatically extracts trace_id, span_id, and other fields from the current span
    and injects them into log records, enabling automatic correlation between logs and traces.

    This filter builds the context dynamically on each call to ensure that any context
    changes (e.g., account_id updated after authentication) are immediately reflected
    in subsequent log records.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Inject trace context information into the log record.

        Builds the context dynamically on each call to ensure freshness.

        Args:
            record: The log record object.

        Returns:
            bool: Always returns True, indicating the log should be recorded.
        """
        # Build context dynamically to ensure freshness
        context = _get_log_context()

        # Apply context fields to the log record
        record.trace_id = context.get("trace_id", "")
        record.span_id = context.get("span_id", "")
        record.request_id = context.get("request_id", "")
        record.operation = context.get("operation", "")
        record.account_id = context.get("account_id", "")
        record.user_id = context.get("user_id", "")
        record.agent_id = context.get("agent_id", "")

        return True


class LogToSpanEventFilter(logging.Filter):
    """
    Log filter that automatically maps log records to OTel span events.

    This filter allows developers to only write log.xxx() calls, and the logs
    will automatically be recorded as span events when OTel is available.
    This eliminates the need to write duplicate code for both logging and tracing.

    Key features:
    - Logs are automatically added as span events when a span is active
    - Exceptions are automatically recorded with record_exception()
    - Extra fields from log records are included as span attributes
    - Best-effort only: never breaks logging if OTel is not available

    Usage:
        handler.addFilter(LogToSpanEventFilter(
            min_level=logging.INFO,
            excluded_loggers={"uvicorn.access", "uvicorn.error"}
        ))
    """

    def __init__(
        self,
        name: str = "",
        min_level: int = logging.INFO,
        excluded_loggers: Optional[set] = None,
    ):
        """
        Initialize the LogToSpanEventFilter.

        Args:
            name: Filter name.
            min_level: Minimum log level to be mapped to span events.
                Logs below this level will only be recorded, not added as span events.
            excluded_loggers: Set of logger names to exclude from span event mapping.
                Useful for excluding noisy loggers like access logs.
        """
        super().__init__(name)
        self._min_level = min_level
        self._excluded_loggers = excluded_loggers or set()

    def _extract_attributes(self, record: logging.LogRecord) -> dict:
        """
        Extract span attributes from a log record.

        Extracts standard fields and extra fields from the log record
        to be used as span event attributes.

        Args:
            record: The log record object.

        Returns:
            Dictionary of attributes to be included in the span event.
        """
        attributes = {
            "log.level": record.levelname,
            "log.logger": record.name,
        }

        # Add location information if available
        if record.pathname:
            attributes["code.filepath"] = record.pathname
        if record.lineno:
            attributes["code.lineno"] = record.lineno
        if record.funcName:
            attributes["code.function"] = record.funcName

        # Add extra fields from the log record
        # Standard fields to exclude from extra attributes
        standard_fields = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "asctime",
            "trace_id",
            "span_id",
            "request_id",
            "operation",
            "account_id",
            "user_id",
            "agent_id",
        }

        for key, value in record.__dict__.items():
            if key not in standard_fields and not key.startswith("_"):
                # Convert to OTel-compatible types
                if isinstance(value, (str, int, float, bool)):
                    attributes[key] = value
                elif value is not None:
                    attributes[key] = str(value)

        return attributes

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Map log record to span event (best-effort).

        This method is called for each log record. It:
        1. Checks if the logger should be excluded
        2. Checks if the log level is above the minimum threshold
        3. If OTel is available and a span is active, adds the log as a span event
        4. For exceptions, also records the exception with record_exception()

        Args:
            record: The log record object.

        Returns:
            bool: Always returns True, indicating the log should be recorded.
        """
        # Check if this logger should be excluded
        if record.name in self._excluded_loggers:
            return True

        # Check if log level is below the minimum threshold for span events
        if record.levelno < self._min_level:
            return True

        # Check if OTel is available
        if otel_trace is None:
            return True

        try:
            # Get the current span
            current_span = otel_trace.get_current_span()
            if current_span is None or not current_span.is_recording():
                return True

            # Extract attributes from the log record
            attributes = self._extract_attributes(record)

            # Handle exceptions specially
            if record.exc_info is not None:
                exc_type, exc_value, exc_tb = record.exc_info
                if exc_value is not None:
                    # Record the exception in the span
                    current_span.record_exception(exc_value, attributes=attributes)

                    # Set span status to ERROR if Status/StatusCode are available
                    if Status is not None and StatusCode is not None:
                        try:
                            current_span.set_status(
                                Status(StatusCode.ERROR, description=str(exc_value))
                            )
                        except Exception:
                            pass
            else:
                # Add as a regular span event
                event_name = record.getMessage() or "log"
                current_span.add_event(event_name, attributes=attributes)

        except Exception:
            # Best-effort only: never break logging
            pass

        return True


def _load_log_config() -> Tuple[str, str, str, Optional[Any]]:
    config = None
    try:
        from openviking_cli.utils.config import get_openviking_config

        config = get_openviking_config()
        log_level_str = config.log.level.upper()
        log_format = config.log.format
        log_output = config.log.output

        if log_output == "file":
            workspace_path = Path(config.storage.workspace).resolve()
            log_dir = workspace_path / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_output = str(log_dir / "openviking.log")
    except Exception:
        log_level_str = "INFO"
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        log_output = "stdout"

    return log_level_str, log_format, log_output, config


def _create_log_handler(log_output: str, config: Optional[Any]) -> logging.Handler:
    # Prevent creating a file literally named "file"
    if log_output == "file":
        log_output = "stdout"

    if log_output == "stdout":
        return logging.StreamHandler(sys.stdout)
    elif log_output == "stderr":
        return logging.StreamHandler(sys.stderr)
    else:
        if config is not None:
            try:
                log_rotation = config.log.rotation
                if log_rotation:
                    log_rotation_days = config.log.rotation_days
                    log_rotation_interval = config.log.rotation_interval

                    if log_rotation_interval == "midnight":
                        when = "midnight"
                        interval = 1
                    else:
                        when = log_rotation_interval
                        interval = 1

                    return TimedRotatingFileHandler(
                        log_output,
                        when=when,
                        interval=interval,
                        backupCount=log_rotation_days,
                        encoding="utf-8",
                    )
                else:
                    return logging.FileHandler(log_output, encoding="utf-8")
            except Exception:
                return logging.FileHandler(log_output, encoding="utf-8")
        else:
            return logging.FileHandler(log_output, encoding="utf-8")


def get_logger(
    name: str = "openviking",
    format_string: Optional[str] = None,
    add_otel_handler: bool = False,
) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: Logger name, defaults to "openviking".
        format_string: Custom log format string, optional.
        add_otel_handler: Whether to add OTel LoggingHandler, defaults to False.

    Returns:
        logging.Logger: Configured logger instance with automatic trace context injection.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        log_level_str, log_format, log_output, config = _load_log_config()
        level = getattr(logging, log_level_str, logging.INFO)
        handler = _create_log_handler(log_output, config)

        if format_string is None:
            format_string = log_format
        formatter = logging.Formatter(format_string)
        handler.setFormatter(formatter)

        # Add unified trace context filter
        handler.addFilter(TraceContextFilter())

        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(level)

    # If OTel log export is globally initialized, attach the handler automatically.
    if add_otel_handler or _otel_log_handler_initialized:
        add_otel_log_handler_to_logger(logger)

    return logger


# Default logger instance
default_logger = get_logger()


def configure_uvicorn_logging() -> None:
    """Configure Uvicorn logging to use OpenViking's logging configuration.

    Configures the 'uvicorn', 'uvicorn.error', and 'uvicorn.access' loggers
    to use the same handlers, format, and trace context injection as openviking logs.
    """
    log_level_str, log_format, log_output, config = _load_log_config()
    level = getattr(logging, log_level_str, logging.INFO)
    handler = _create_log_handler(log_output, config)
    formatter = logging.Formatter(log_format)
    handler.setFormatter(formatter)

    # Add unified trace context filter
    handler.addFilter(TraceContextFilter())

    # Configure all Uvicorn loggers
    uvicorn_logger_names = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for logger_name in uvicorn_logger_names:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
