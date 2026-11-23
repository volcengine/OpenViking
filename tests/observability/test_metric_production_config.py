"""Kafka reporting uses the configuration loaded from ov.conf."""

import json
from unittest.mock import Mock

import pytest

from openviking.config.config_file import preview_config_file
from openviking.observability.events import try_publish_event
from openviking.observability.reporting import runtime
from openviking.server.config import EventReportingConfig, load_server_config
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def reporting_ov_conf(tmp_path, monkeypatch):
    monkeypatch.setenv("NACOS_CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("NACOS_SERVER_ADDR", "http://127.0.0.1:1")
    monkeypatch.setenv("OV_REPORTING_TEST_FRAGMENT", 'replacement-with-"quotes"')
    monkeypatch.setenv("OV_REPORTING_TEST_PORT", "1944")
    monkeypatch.setenv("OV_REPORTING_TEST_MODEL_KEY", "business-key")
    reporting = {
        "enabled": True,
        "kafka": {
            "bootstrap_servers": "broker:9092",
            "topic": "metric-log",
            "security_protocol": "SASL_SSL",
            "username": "kafka-user",
            "password": "literal-${OV_REPORTING_TEST_FRAGMENT}",
        },
        "telemetry": {
            "enabled": True,
            "queue_capacity": 32,
            "deployment": {"account_id": "cloud", "resource_id": "library", "region": "test"},
        },
    }
    content = json.dumps(
        {
            "server": {
                "port": "UNQUOTED_PORT",
                "observability": {"reporting": reporting},
            },
            "storage": {"workspace": str(tmp_path / "data")},
            "vlm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "${OV_REPORTING_TEST_MODEL_KEY}",
            },
        }
    ).replace('"UNQUOTED_PORT"', "${OV_REPORTING_TEST_PORT}")
    path = tmp_path / "ov.conf"
    path.write_text(content, encoding="utf-8")
    return path, content, reporting


@pytest.mark.asyncio
async def test_reporting_starts_from_ov_conf(reporting_ov_conf, monkeypatch):
    path, _, reporting = reporting_ov_conf
    reporter = Mock()
    create_reporter = Mock(return_value=reporter)
    monkeypatch.setattr(runtime, "KafkaEventReporter", create_reporter)

    config = load_server_config(str(path))
    assert config.port == 1944
    try:
        assert await runtime.start_reporting(config.observability.reporting) is reporter
        create_reporter.assert_called_once_with(
            {"kafka": reporting["kafka"], "telemetry": reporting["telemetry"]}
        )
        try_publish_event("http.request", {"method": "GET", "route": "/health", "status": 200})
        payload = reporter.report.call_args.args[0]
        assert payload["event_name"] == "http_request"
        assert payload["account_id"] == "cloud"
        assert payload["resource_id"] == "library"
    finally:
        await runtime.stop_reporting()


def test_config_readers_preserve_literal_credentials(reporting_ov_conf, monkeypatch):
    path, content, _ = reporting_ov_conf
    for field in ("_instance", "_config_file", "_config_file_revision", "_config_file_content"):
        monkeypatch.setattr(
            OpenVikingConfigSingleton, field, getattr(OpenVikingConfigSingleton, field)
        )
    config = OpenVikingConfigSingleton.initialize(config_path=str(path))
    assert config.vlm.api_key == "business-key"
    assert preview_config_file(content)["content"] == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [EventReportingConfig(telemetry={"enabled": True}), EventReportingConfig(enabled=True)],
)
async def test_reporting_requires_both_enable_switches(settings, monkeypatch):
    create_reporter = Mock()
    monkeypatch.setattr(runtime, "KafkaEventReporter", create_reporter)

    assert await runtime.start_reporting(settings) is None
    create_reporter.assert_not_called()
