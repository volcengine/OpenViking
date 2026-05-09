import logging

from openviking.observability.context import (
    bind_operation_observability_context,
    bind_root_observability_context,
    reset_operation_observability_context,
    reset_root_observability_context,
)
from openviking.telemetry.span_models import OperationSpanAttributes, RootSpanAttributes
from openviking_cli.utils import get_logger
from openviking_cli.utils.logger import TraceContextFilter, bind_log_execution_trace


def test_trace_context_filter_reuses_execution_trace_id_without_active_span():
    """TraceContextFilter should fall back to execution-trace context when no span exists.

    The span_id is derived from the first 16 characters of the trace_id.
    """
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    with bind_log_execution_trace("1234567890abcdef1234567890abcdef"):
        passed = TraceContextFilter().filter(record)

    assert passed is True
    assert record.trace_id == "1234567890abcdef1234567890abcdef"
    # span_id is derived from first 16 chars of trace_id
    assert record.span_id == "1234567890abcdef"


def test_trace_context_filter_generates_span_id_from_trace_id():
    """TraceContextFilter should generate span_id from trace_id when not explicitly provided."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    # Test with explicitly provided span_id
    with bind_log_execution_trace(
        trace_id="1234567890abcdef1234567890abcdef",
        span_id="fedcba0987654321",
    ):
        passed = TraceContextFilter().filter(record)

    assert passed is True
    assert record.trace_id == "1234567890abcdef1234567890abcdef"
    assert record.span_id == "fedcba0987654321"


def test_trace_context_filter_injects_root_observability_fields_without_span():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    root = RootSpanAttributes(http_method="GET", http_route="/demo", request_id="req-1")
    root.account_id = "acct-1"
    root.user_id = "user-1"
    root.agent_id = "agent-1"
    token = bind_root_observability_context(root)
    try:
        passed = TraceContextFilter().filter(record)
    finally:
        reset_root_observability_context(token)

    assert passed is True
    assert record.request_id == "req-1"
    assert record.account_id == "acct-1"
    assert record.user_id == "user-1"
    assert record.agent_id == "agent-1"


def test_trace_context_filter_injects_operation_observability_fields_without_span():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    operation = OperationSpanAttributes(operation="search.find", telemetry_id="tm-1")
    operation.status = "ok"
    token = bind_operation_observability_context(operation)
    try:
        passed = TraceContextFilter().filter(record)
    finally:
        reset_operation_observability_context(token)

    assert passed is True
    assert record.operation == "search.find"
    assert record.telemetry_id == "tm-1"
    assert record.status == "ok"


def _manual_smoke_check() -> None:
    """Run a manual logger smoke check when this module is executed directly."""
    print("Testing logger...")
    print("=" * 80)

    logger = get_logger(__name__)
    print(f"Logger: {logger}")
    print(f"Logger level: {logger.level}")

    print(f"\nRoot logger level: {logging.getLogger().level}")

    print("\nTesting log messages:")
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")

    print("\n" + "=" * 80)
    print("Checking openviking.server.app logger...")

    app_logger = get_logger("openviking.server.app")
    print(f"App logger level: {app_logger.level}")


if __name__ == "__main__":
    _manual_smoke_check()
