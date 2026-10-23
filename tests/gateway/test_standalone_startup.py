"""Standalone startup waits for transient failures and configures HTTP keep-alive."""

import json
import logging
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
    health, sleep = probe
    ready = {"status": "ok", "auth_mode": "api_key"}
    health.side_effect = [error] * 7 + [ready]
    config = StandaloneGatewayConfig(openviking_url="http://ov.test", host="0.0.0.0")

    assert await standalone.check_openviking(config) == ready

    assert health.await_args_list == [call(require_identity=False)] * 8
    assert sleep.await_args_list == [call(delay) for delay in (1, 2, 4, 8, 16, 30, 30)]
    assert len(caplog.records) == 7
    assert all(record.levelno == logging.WARNING for record in caplog.records)
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
