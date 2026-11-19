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
    token = bind_root_observability_context(root)
    try:
        passed = TraceContextFilter().filter(record)
    finally:
        reset_root_observability_context(token)

    assert passed is True
    assert record.request_id == "req-1"
    assert record.account_id == "acct-1"
    assert record.user_id == "user-1"


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


def test_common_formatter_uses_utc_milliseconds_and_one_physical_line():
    from openviking_cli.utils.logger import CommonLogFormatter

    record = logging.LogRecord(
        "test.component", logging.WARNING, __file__, 1, "line1\nline2\x1b", (), None
    )
    record.created = 0.012
    record.total = 2
    record.degraded = True
    line = CommonLogFormatter().format(record)
    assert line.startswith("1970-01-01T00:00:00.012Z WARNING test.component pid=")
    assert 'request_id=- trace_id=- message="line1\\nline2\\u001b"' in line
    assert "total=2" in line and "degraded=true" in line
    assert len(line.splitlines()) == 1
    assert "\x1b" not in line


def test_safe_log_filter_omits_unknown_fields_and_exception_text():
    from openviking_cli.utils.logger import CommonLogFormatter, _SafeLogRecordFilter

    class PrivateObject:
        def __str__(self):
            raise AssertionError("private object must not be rendered")

    secret = "private-body /private/sensitive/path private-peer"
    record = logging.LogRecord(
        "test.component",
        logging.ERROR,
        __file__,
        1,
        "failure=%s",
        (RuntimeError(secret),),
        (RuntimeError, RuntimeError(secret), None),
    )
    record.peer_id = secret
    record.api_key = secret
    record.content = PrivateObject()
    record.total = 10**1000
    _SafeLogRecordFilter().filter(record)
    line = CommonLogFormatter().format(record)
    assert "RuntimeError" in line
    assert record.exc_info is None
    assert secret not in line
    assert not hasattr(record, "peer_id") and not hasattr(record, "api_key")
    assert not hasattr(record, "content") and not hasattr(record, "total")


def test_server_root_configuration_is_idempotent_and_sdk_preserves_host(tmp_path):
    import subprocess
    import sys
    import textwrap

    output = tmp_path / "server.log"
    script = textwrap.dedent("""
        import logging, sys
        from openviking_cli.utils import logger as module
        from openviking.observability.log_safety import DEFAULT_LOG_FORMAT
        module._load_log_config = lambda: ('INFO', DEFAULT_LOG_FORMAT, sys.argv[1], None)
        root = logging.getLogger()
        host = logging.StreamHandler()
        root.handlers = [host]
        root.setLevel(logging.ERROR)
        module.get_logger('openviking.sdk').info('sdk event')
        assert root.handlers == [host] and root.level == logging.ERROR
        for _ in range(3):
            module.configure_server_logging()
            module.get_logger('openviking.server.probe').info('owned event')
        logging.getLogger('mcp.probe').warning('third party warning')
        dependency = logging.getLogger('LiteLLM')
        dependency.addHandler(logging.StreamHandler(sys.stdout))
        dependency.warning('late dependency handler')
        module._stop_std_stream_listeners()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(output)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    lines = output.read_text().splitlines()
    assert sum('message="sdk event"' in line for line in lines) == 1
    assert sum('message="owned event"' in line for line in lines) == 3
    assert sum("WARNING mcp.probe" in line for line in lines) == 1
    assert sum('message="late dependency handler"' in line for line in lines) == 1
    assert result.stdout == ""
    assert all(
        " pid=" in line and " request_id=" in line and " trace_id=" in line for line in lines
    )


def test_log_config_normalizes_levels_and_rejects_unsupported_formats():
    import pytest

    from openviking.observability.log_safety import DEFAULT_LOG_FORMAT, LEGACY_LOG_FORMAT
    from openviking_cli.utils.config.log_config import LogConfig

    assert LogConfig().level == "INFO"
    assert LogConfig(level="warn").level == "WARNING"
    assert LogConfig(level="fatal").level == "CRITICAL"
    assert LogConfig(format=LEGACY_LOG_FORMAT).format == DEFAULT_LOG_FORMAT
    with pytest.raises(ValueError):
        LogConfig(level="unexpected")
    with pytest.raises(ValueError):
        LogConfig(format="%(message)s")


def test_otel_destination_filters_fields_thresholds_and_duplicate_propagation(tmp_path):
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import logging, sys
        from openviking_cli.utils import logger as module
        from openviking.observability.log_safety import DEFAULT_LOG_FORMAT
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor, InMemoryLogRecordExporter
        exporter = InMemoryLogRecordExporter()
        provider = LoggerProvider()
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        handler = LoggingHandler(logger_provider=provider)
        handler.addFilter(module._ConfiguredLevelFilter(logging.ERROR))
        handler.addFilter(module.TraceContextFilter())
        handler.addFilter(module._SafeLogRecordFilter())
        module._otel_log_handler = handler
        module._load_log_config = lambda: ('ERROR', DEFAULT_LOG_FORMAT, sys.argv[1], None)
        module.configure_server_logging()
        module.attach_otel_log_handler_to_existing_loggers()
        logging.getLogger('mcp.probe').warning('filtered')
        private = RuntimeError('private-exception-body')
        logging.getLogger('mcp.probe').error('failure=%(exc)s', {'exc': private},
            exc_info=(RuntimeError, private, None), extra={'api_key': 'private-key'})
        module.get_logger('openviking.probe').error('owned error', extra={'error_code': 'PROBE_FAILED'})
        records = [item.log_record for item in exporter.get_finished_logs()]
        assert len(records) == 2
        failure = records[0]
        assert failure.body == 'failure=RuntimeError'
        assert 'api_key' not in failure.attributes
        assert 'exception.message' not in failure.attributes
        assert not failure.attributes.get('code.file.path')
        assert failure.attributes['error_type'] == 'RuntimeError'
        assert records[1].attributes['error_code'] == 'PROBE_FAILED'
        provider.shutdown()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "otel.log")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_log_field_transformations_omit_private_fields_and_hash_paths():
    from openviking.observability.log_safety import safe_log_fields, safe_uri_for_log

    uri = "viking://user/alice/peers/private-peer/private-content.md"
    safe = safe_log_fields(
        {
            "source_ip": "127.0.0.1, 192.0.2.1",
            "user_agent": "curl/1.2 private-body",
            "target_id": uri,
            "peer_id": "private-peer",
            "content": "private-content",
        }
    )
    assert "source_ip" not in safe and "user_agent" not in safe
    assert safe["target_id"].startswith("viking://user/alice/hash/")
    assert safe_uri_for_log(safe["target_id"]) == safe["target_id"]
    assert "private-peer" not in str(safe) and "private-content" not in str(safe)


def test_cpp_public_logging_uses_common_default(tmp_path):
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import sys
        from openviking.storage.vectordb.engine import init_logging, ENGINE_VARIANT
        from openviking.observability.log_safety import DEFAULT_LOG_FORMAT
        if ENGINE_VARIANT == 'unavailable':
            raise SystemExit(77)
        init_logging('INFO', sys.argv[1])
        init_logging('INFO', 'stderr')
        init_logging('INFO', 'stdout', DEFAULT_LOG_FORMAT)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "native.log")],
        capture_output=True,
        text=True,
    )
    if result.returncode == 77:
        import pytest

        pytest.skip("native engine is not built")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "native.log").exists()
