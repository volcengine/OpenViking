"""Standalone startup waits for transient failures and configures HTTP keep-alive."""

import json
import logging
import os
import re
import sys
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking_gateway import cli, standalone
from openviking_gateway.client import VikingError
from openviking_gateway.config import StandaloneGatewayConfig


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for field in StandaloneGatewayConfig.model_fields:
        monkeypatch.delenv("OPENVIKING_GATEWAY_" + field.upper(), raising=False)


@pytest.fixture
def probe(monkeypatch):
    health, sleep = AsyncMock(), AsyncMock()
    monkeypatch.setattr(standalone.VikingClient, "health", health)
    monkeypatch.setattr(standalone.asyncio, "sleep", sleep)
    return health, sleep


@pytest.mark.parametrize(
    "error",
    [
        VikingError("openviking_unavailable"),
        VikingError("openviking_http_500", 500),
    ],
)
async def test_public_startup_waits_with_capped_backoff(probe, caplog, error):
    caplog.set_level(logging.INFO, logger="openviking_gateway.standalone")
    health, sleep = probe
    ready = {"status": "ok", "auth_mode": "api_key"}
    health.side_effect = [error] * 7 + [ready]
    config = StandaloneGatewayConfig(openviking_url="http://ov.test", host="0.0.0.0")

    assert await standalone.check_openviking(config) == ready

    assert health.await_args_list == [call(require_identity=False)] * 8
    assert sleep.await_args_list == [call(delay) for delay in (1, 2, 4, 8, 16, 30, 30)]
    assert len(caplog.records) == 7
    assert all(record.levelno == logging.INFO for record in caplog.records)
    assert error.reason in caplog.text


@pytest.mark.parametrize(
    "result, message",
    [
        ({"auth_mode": "dev"}, "successful synchronous probe"),
        ({"status": "ok"}, "successful synchronous probe"),
        (VikingError("trusted_mode_unsupported", 403), "does not support trusted"),
    ],
)
async def test_public_recovery_still_requires_api_key_auth(probe, result, message):
    health, sleep = probe
    health.side_effect = [VikingError("openviking_http_503", 503), result]
    config = StandaloneGatewayConfig(openviking_url="http://ov.test", host="0.0.0.0")

    with pytest.raises(ValueError, match=message):
        await standalone.check_openviking(config)

    sleep.assert_awaited_once_with(1)
    assert health.await_count == 2


@pytest.mark.parametrize("status", [302, 403, 404, 429])
async def test_public_startup_rejects_nontransient_http_errors(probe, status):
    health, sleep = probe
    health.side_effect = VikingError(f"openviking_http_{status}", status)
    config = StandaloneGatewayConfig(openviking_url="http://ov.test", host="0.0.0.0")

    with pytest.raises(ValueError, match="successful synchronous probe"):
        await standalone.check_openviking(config)

    health.assert_awaited_once_with(require_identity=False)
    sleep.assert_not_awaited()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
async def test_loopback_startup_does_not_wait(probe, host):
    health, sleep = probe
    health.side_effect = VikingError("openviking_unavailable")
    config = StandaloneGatewayConfig(openviking_url="http://ov.test", host=host)

    assert await standalone.check_openviking(config) == {
        "status": "degraded",
        "reason": "openviking_unavailable",
    }
    health.assert_awaited_once_with(require_identity=False)
    sleep.assert_not_awaited()


def test_main_passes_keep_alive_to_uvicorn(tmp_path, monkeypatch):
    path = tmp_path / "gateway.conf"
    path.write_text(json.dumps({"openviking_url": "http://ov.test", "keep_alive_seconds": 90.5}))
    monkeypatch.setattr(sys, "argv", ["openviking-gateway", "--config", str(path)])
    check, run = AsyncMock(), Mock()
    monkeypatch.setattr(cli, "check_openviking", check)
    monkeypatch.setattr("uvicorn.run", run)

    cli.main()

    check.assert_awaited_once()
    assert run.call_args.kwargs["timeout_keep_alive"] == 90.5


def test_log_path_uses_common_output(tmp_path, monkeypatch):
    from openviking_gateway.client import CALL, CallContext
    from openviking_gateway.sinks import configure_logging

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    path = tmp_path / "gateway.log"
    listener = configure_logging(str(path))
    try:
        log = logging.getLogger("openviking_gateway.test")
        token = CALL.set(CallContext("req-1"))
        try:
            log.warning("upstream %s failed", "chat")
            try:
                raise ValueError("boom")
            except ValueError:
                log.exception("request failed")
        finally:
            CALL.reset(token)
        logging.getLogger("server.test").info("Started server process")
    finally:
        listener.stop()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])
    lines = path.read_text().splitlines()
    headers = [line.split(" message=", 1)[0].split() for line in lines]
    assert [(h[1], h[2]) for h in headers] == [
        ("WARNING", "openviking_gateway.test"),
        ("ERROR", "openviking_gateway.test"),
        ("INFO", "server.test"),
    ]
    assert [h[4] for h in headers] == ["request_id=req-1", "request_id=req-1", "request_id=-"]
    assert all(h[3] == f"pid={os.getpid()}" and h[5] == "trace_id=-" for h in headers)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", h[0]) for h in headers)
    assert "boom" not in path.read_text() and "\x1b" not in path.read_text()

    # The host installs it in each worker and lets uvicorn's loggers reach it.
    config = tmp_path / "gateway.conf"
    config.write_text(json.dumps({"openviking_url": "http://ov.test", "log_path": str(path)}))
    monkeypatch.setattr(sys, "argv", ["openviking-gateway", "--config", str(config)])
    configure, run = Mock(), Mock()
    monkeypatch.setattr(cli, "check_openviking", AsyncMock())
    monkeypatch.setattr(cli, "configure_logging", configure)
    monkeypatch.setattr(cli, "create_standalone_app", Mock())
    monkeypatch.setattr("uvicorn.run", run)
    cli.main()
    assert run.call_args.kwargs["log_config"] is None
    cli.factory()
    assert configure.call_args.args == (str(path),)


def test_no_log_path_uses_common_uvicorn_logging(tmp_path, monkeypatch):
    config = tmp_path / "gateway.conf"
    config.write_text(json.dumps({"openviking_url": "http://ov.test"}))
    monkeypatch.setattr(sys, "argv", ["openviking-gateway", "--config", str(config)])
    configure, run = Mock(), Mock()
    monkeypatch.setattr(cli, "check_openviking", AsyncMock())
    monkeypatch.setattr(cli, "configure_logging", configure)
    monkeypatch.setattr(cli, "create_standalone_app", Mock())
    monkeypatch.setattr("uvicorn.run", run)
    cli.main()
    cli.factory()
    assert run.call_args.kwargs["log_config"] is None
    assert configure.call_args.args == ("",)
