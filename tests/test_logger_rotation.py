import logging

from openviking_cli.utils import logger as logger_module


def test_timed_rotation_failure_keeps_file_logging_alive(tmp_path, monkeypatch, capsys):
    log_path = tmp_path / "server.log"
    handler = logger_module._RustAwareTimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.rolloverAt = 0

    def fail_rollover(_self):
        if _self.stream is not None:
            _self.stream.close()
            _self.stream = None
        raise OSError("simulated Windows rename failure")

    monkeypatch.setattr(
        logger_module.TimedRotatingFileHandler,
        "doRollover",
        fail_rollover,
    )

    try:
        handler.emit(logging.makeLogRecord({"levelno": logging.INFO, "msg": "first"}))
        handler.emit(logging.makeLogRecord({"levelno": logging.INFO, "msg": "second"}))
        handler.flush()
    finally:
        handler.close()

    assert log_path.read_text(encoding="utf-8") == "first\nsecond\n"
    assert "log rotation failed" in capsys.readouterr().err


def test_timed_rotation_still_reopens_rust_tracing_on_success(tmp_path, monkeypatch):
    calls = []
    handler = logger_module._RustAwareTimedRotatingFileHandler(
        tmp_path / "server.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )

    monkeypatch.setattr(logger_module, "_reopen_rust_tracing_file", lambda: calls.append("reopened"))

    try:
        handler.doRollover()
    finally:
        handler.close()

    assert calls == ["reopened"]
