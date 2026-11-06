from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from openviking.metrics.datasources import EmbeddingEventDataSource
from openviking.models.embedder.volcengine_embedders import _record_failed_embedding_call
from openviking.models.vlm.base import VLMBase
from openviking.observability.events import get_event_bus, register_event_subscriber
from openviking.observability.reporting import config as reporting_config
from openviking.observability.reporting import runtime
from openviking.server.config import EventReportingConfig
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
    register_event_subscriber("test_export", runtime.make_subscriber(capture, {
        "telemetry": {"model_cost_bearers": [
            {"provider": "openai", "model_name": "same_endpoint", "cost_bearer": "ov"},
        ]},
    }))
    return capture


def test_vlm_owner_follows_each_credential_even_for_same_endpoint(monkeypatch):
    capture = capture_export(monkeypatch)
    cfg = VLMConfig(model="same_endpoint", credentials=[
        {"id": owner, "provider": "openai", "api_key": "test-key", "cost_bearer": owner}
        for owner in ["customer", "ov", "unknown"]
    ])
    for cred in cfg.credentials:
        backend = MetricVLM(cfg.build_vlm_config_dict_for_credential(cred))
        backend.update_token_usage("same_endpoint", "openai", 11, 3, duration_seconds=0.1)
        backend.record_failed_call(duration_seconds=0.2, error=TimeoutError("request failed"))
    assert [e["cost_bearer"] for e in capture.events] == [
        "customer", "customer", "ov", "ov", "unknown", "unknown"
    ]
    assert [e["prompt_tokens"] for e in capture.events] == [11, 0, 11, 0, 11, 0]


def test_embedding_owner_reaches_actual_retry_and_failover_children(monkeypatch):
    capture = capture_export(monkeypatch)
    cfg = EmbeddingConfig(dense=EmbeddingModelConfig(
        provider="openai", model="text-embedding-3-small", dimension=1536,
        credentials=[
            {"id": owner, "api_key": "test-key", "cost_bearer": owner}
            for owner in ["customer", "ov"]
        ],
    ))
    embedder = cfg.get_embedder()
    try:
        for candidate in embedder._embedders:
            candidate.inner.update_token_usage("same_endpoint", "openai", 7, 0, duration_seconds=0.1)
            _record_failed_embedding_call(candidate.inner, duration_seconds=0.2, error=TimeoutError("failed"))
        assert [e["cost_bearer"] for e in capture.events] == ["customer", "customer", "ov", "ov"]
    finally:
        embedder.close()


def test_unknown_or_invalid_owner_is_not_silently_billed_to_platform(monkeypatch):
    capture = capture_export(monkeypatch)
    for owner in ["unknown", "invalid"]:
        EmbeddingEventDataSource.record_call(provider="openai", model_name="same_endpoint",
            duration_seconds=0.1, prompt_tokens=5, completion_tokens=0, cost_bearer=owner)
    assert [e["cost_bearer"] for e in capture.events] == ["unknown", "unknown"]
    with pytest.raises(ValidationError):
        VLMCredential(cost_bearer="invalid")


@pytest.mark.parametrize("explicit", [False, True])
def test_reporting_uses_environment_location_with_explicit_config_precedence(monkeypatch, explicit):
    monkeypatch.delenv("NACOS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("NACOS_USERNAME", raising=False)
    for key, value in {"NACOS_SERVER_ADDR":"http://config-center:8848", "NACOS_NAMESPACE":"environment",
                       "NACOS_DATA_ID":"environment_config", "NACOS_GROUP":"environment_group"}.items():
        monkeypatch.setenv(key, value)
    seen = []

    class Opener:
        def open(self, request, **kwargs):
            seen.append(request)
            return io.BytesIO(json.dumps({"kafka":{"topic":"metric-log"}, "telemetry":{"enabled":True},
                "connections":{"other":{"password":"${UNAVAILABLE_OTHER_PASSWORD}"}}}).encode())

    monkeypatch.setattr(reporting_config, "build_opener", lambda *args: Opener())
    settings = EventReportingConfig(**({"nacos_namespace":"explicit", "nacos_data_id":"explicit_config",
                                       "nacos_group":"explicit_group"} if explicit else {}))
    config = reporting_config.load_configuration(settings)
    params = parse_qs(urlsplit(seen[0]).query)
    prefix = "explicit" if explicit else "environment"
    assert params == {"tenant":[prefix], "dataId":[prefix+"_config"], "group":[prefix+"_group"]}
    assert set(config) == {"kafka", "telemetry"}


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
