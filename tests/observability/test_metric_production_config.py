from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openviking.metrics.datasources import EmbeddingEventDataSource
from openviking.models.embedder.volcengine_embedders import _record_failed_embedding_call
from openviking.models.vlm.base import VLMBase
from openviking.observability.events import (
    get_event_bus,
    register_event_subscriber,
    try_publish_event,
)
from openviking.observability.reporting import config as reporting_config
from openviking.observability.reporting import runtime
from openviking.server.config import EventReportingConfig, load_server_config
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig
from openviking_cli.utils.config.vlm_config import VLMConfig, VLMCredential


class Capture:
    def __init__(self):
        self.events = []

    def report(self, payload):
        self.events.append(dict(payload))


class MetricVLM(VLMBase):
    def get_completion(self, *args, **kwargs):
        raise NotImplementedError

    async def get_completion_async(self, *args, **kwargs):
        raise NotImplementedError

    def get_vision_completion(self, *args, **kwargs):
        raise NotImplementedError

    async def get_vision_completion_async(self, *args, **kwargs):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def isolate_metric_events():
    get_event_bus().clear()
    yield
    get_event_bus().clear()


def capture_export(monkeypatch):
    monkeypatch.setenv("OV_MODEL_COST_BEARER", "customer")
    capture = Capture()
    # Deliberately contradictory legacy mapping must not win over each call.
    register_event_subscriber(
        "test_export",
        runtime.make_subscriber(
            capture,
            {
                "telemetry": {
                    "model_cost_bearers": [
                        {"provider": "openai", "model_name": "same_endpoint", "cost_bearer": "ov"},
                    ]
                },
            },
        ),
    )
    return capture


def test_vlm_owner_follows_each_credential_even_for_same_endpoint(monkeypatch):
    capture = capture_export(monkeypatch)
    cfg = VLMConfig(
        model="same_endpoint",
        credentials=[
            {"id": owner, "provider": "openai", "api_key": "test-key", "cost_bearer": owner}
            for owner in ["customer", "ov", "unknown"]
        ],
    )
    for cred in cfg.credentials:
        backend = MetricVLM(cfg.build_vlm_config_dict_for_credential(cred))
        backend.update_token_usage("same_endpoint", "openai", 11, 3, duration_seconds=0.1)
        backend.record_failed_call(duration_seconds=0.2, error=TimeoutError("request failed"))
    assert [e["cost_bearer"] for e in capture.events] == [
        "customer",
        "customer",
        "ov",
        "ov",
        "unknown",
        "unknown",
    ]
    assert [e["prompt_tokens"] for e in capture.events] == [11, None, 11, None, 11, None]
    assert [e["completion_tokens"] for e in capture.events] == [3, None, 3, None, 3, None]


def test_embedding_owner_reaches_actual_retry_and_failover_children(monkeypatch):
    capture = capture_export(monkeypatch)
    cfg = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
            credentials=[
                {"id": owner, "api_key": "test-key", "cost_bearer": owner}
                for owner in ["customer", "ov"]
            ],
        )
    )
    embedder = cfg.get_embedder()
    try:
        for candidate in embedder._embedders:
            candidate.inner.update_token_usage(
                "same_endpoint", "openai", 7, 0, duration_seconds=0.1
            )
            _record_failed_embedding_call(
                candidate.inner, duration_seconds=0.2, error=TimeoutError("failed")
            )
        assert [e["cost_bearer"] for e in capture.events] == ["customer", "customer", "ov", "ov"]
    finally:
        embedder.close()


def test_unknown_or_invalid_owner_is_not_silently_billed_to_platform(monkeypatch):
    capture = capture_export(monkeypatch)
    for owner in ["unknown", "invalid"]:
        EmbeddingEventDataSource.record_call(
            provider="openai",
            model_name="same_endpoint",
            duration_seconds=0.1,
            prompt_tokens=5,
            completion_tokens=0,
            cost_bearer=owner,
        )
    assert [e["cost_bearer"] for e in capture.events] == ["unknown", "unknown"]
    with pytest.raises(ValidationError):
        VLMCredential(cost_bearer="invalid")


def test_reporting_reads_ov_conf_and_ignores_nacos(monkeypatch, tmp_path):
    for key, value in {
        "NACOS_SERVER_ADDR": "http://unavailable-config-center:8848",
        "NACOS_CONFIG_FILE": str(tmp_path / "does-not-exist.json"),
        "NACOS_USERNAME": "unused",
        "NACOS_PASSWORD": "unused",
    }.items():
        monkeypatch.setenv(key, value)
    sections = {
        "kafka": {
            "bootstrap_servers": "broker:9092",
            "topic": "metric-log",
            "username": "kms-user",
            "password": "literal-${UNAVAILABLE_PASSWORD}",
            "security_protocol": "SASL_PLAINTEXT",
            "proxy_url": "socks5://proxy:1080",
            "properties": {"sasl.mechanism": "PLAIN"},
        },
        "telemetry": {
            "enabled": True,
            "queue_capacity": 2048,
            "deployment": {
                "account_id": "cloud-account",
                "resource_id": "library",
                "region": "cn-beijing",
            },
        },
    }
    path = tmp_path / "ov.conf"
    path.write_text(
        json.dumps({"server": {"observability": {"reporting": {"enabled": True, **sections}}}})
    )
    settings = load_server_config(str(path)).observability.reporting
    config = reporting_config.load_configuration(settings)
    assert settings.enabled
    assert config == sections
    assert set(config) == {"kafka", "telemetry"}
    config["kafka"]["password"] = "changed"
    config["telemetry"]["deployment"]["account_id"] = "changed"
    assert reporting_config.load_configuration(settings) == sections


@pytest.mark.parametrize(
    "literal",
    [
        "literal-$OV_KMS_LITERAL-suffix",
        "literal-${OV_KMS_LITERAL}-suffix",
        "literal-%OV_KMS_LITERAL%",
    ],
)
def test_reporting_preserves_kms_literals_and_business_environment(monkeypatch, tmp_path, literal):
    from openviking.config.config_file import preview_config_file
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    # The replacement would both change the credential and break raw JSON parsing.
    monkeypatch.setenv("OV_KMS_LITERAL", 'environment-"quote"-\\slash\nnewline')
    monkeypatch.setenv("OV_BUSINESS_TOKEN", "business-token")
    monkeypatch.setenv("OV_BUSINESS_PORT", "1944")
    sections = {
        "kafka": {
            "bootstrap_servers": "broker:9092",
            "topic": literal,
            "username": literal,
            "password": literal,
            "proxy_url": literal,
            "properties": {"sasl.jaas.config": literal},
        },
        "telemetry": {
            "enabled": True,
            "deployment": {"account_id": literal, "resource_id": "library", "region": "cn-beijing"},
        },
    }
    data = {
        "server": {
            "port": "UNQUOTED_PORT",
            "root_api_key": "${OV_BUSINESS_TOKEN}",
            "observability": {"reporting": {"enabled": True, **sections}},
        },
        "vlm": {"provider": "openai", "model": "gpt-4o", "api_key": "${OV_BUSINESS_TOKEN}"},
        "storage": {"workspace": str(tmp_path / "data")},
    }
    content = json.dumps(data).replace('"UNQUOTED_PORT"', "${OV_BUSINESS_PORT}")
    path = tmp_path / "ov.conf"
    path.write_text(content)
    server = load_server_config(str(path))
    assert reporting_config.load_configuration(server.observability.reporting) == sections
    assert server.root_api_key == "business-token"
    assert server.port == 1944
    for field in ("_instance", "_config_file", "_config_file_revision", "_config_file_content"):
        monkeypatch.setattr(
            OpenVikingConfigSingleton, field, getattr(OpenVikingConfigSingleton, field)
        )
    config = OpenVikingConfigSingleton.initialize(config_path=str(path))
    assert config.vlm.api_key == "business-token"
    assert OpenVikingConfigSingleton.get_config_file_content() == content.encode()
    assert preview_config_file(content)["content"] == content
    assert path.read_text() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kernel_enabled,telemetry_enabled", [(False, True), (True, False), (True, True)]
)
async def test_reporting_starts_from_inline_settings(
    monkeypatch, kernel_enabled, telemetry_enabled
):
    settings = EventReportingConfig(
        enabled=kernel_enabled,
        kafka={"bootstrap_servers": "broker:9092", "topic": "metric-log"},
        telemetry={
            "enabled": telemetry_enabled,
            "deployment": {"account_id": "cloud", "resource_id": "library"},
        },
    )
    created = []

    class Reporter(Capture):
        def __init__(self, config):
            super().__init__()
            created.append(config)

        def close(self):
            pass

    monkeypatch.setattr(runtime, "KafkaEventReporter", Reporter)
    try:
        reporter = await runtime.start_reporting(settings)
        if kernel_enabled and telemetry_enabled:
            assert reporter is not None
            assert created == [reporting_config.load_configuration(settings)]
            try_publish_event("http.request", {"method": "GET", "route": "/health", "status": 200})
            assert reporter.events[0]["account_id"] == "cloud"
            assert reporter.events[0]["resource_id"] == "library"
        else:
            assert reporter is None
            assert created == []
    finally:
        await runtime.stop_reporting()


@pytest.mark.asyncio
async def test_reporting_invalid_inline_config_preserves_business_service():
    assert reporting_config.load_configuration(EventReportingConfig()) == {
        "kafka": {},
        "telemetry": {},
    }
    settings = EventReportingConfig(enabled=True, telemetry={"enabled": True})
    try:
        assert await runtime.start_reporting(settings) is None
        assert not runtime.is_reporting_enabled()
    finally:
        await runtime.stop_reporting()


@pytest.mark.parametrize("section", ["vlm", "embedding"])
def test_credential_owner_provisioning_remains_root_only(section):
    from openviking.server.identity import RequestContext, Role, UserIdentifier
    from openviking.server.routers.admin import _authorize_account_config_patch
    from openviking_cli.exceptions import PermissionDeniedError

    binding = {"credentials": [{"provider": "openai", "api_key": "key", "cost_bearer": "ov"}]}
    settings = {section: binding if section == "vlm" else {"dense": binding}}
    for role in (Role.ADMIN, Role.USER):
        ctx = RequestContext(user=UserIdentifier("account", "member"), role=role)
        with pytest.raises(PermissionDeniedError, match="Only ROOT"):
            _authorize_account_config_patch(ctx, settings)
    _authorize_account_config_patch(
        RequestContext(user=UserIdentifier("account", "provisioner"), role=Role.ROOT), settings
    )
