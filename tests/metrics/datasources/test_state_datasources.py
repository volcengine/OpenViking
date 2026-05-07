# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import openviking.metrics.datasources.probes as probes
from openviking.metrics.collectors.base import (
    CollectorConfig,
    ProbeMetricCollector,
    StateMetricCollector,
)
from openviking.metrics.core.base import ReadEnvelope
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.observer_state import VikingDBStateDataSource


class _EnvelopeStateCollector(StateMetricCollector):
    config = CollectorConfig()
    STALE_ON_ERROR = True

    def __init__(self) -> None:
        self.error_seen = False
        self.value_seen = None

    def read_metric_input(self):
        return ReadEnvelope(ok=False, value=("fallback",))

    def collect_hook(self, registry, metric_input) -> None:
        self.value_seen = metric_input

    def collect_stale_hook(self, registry, error: Exception) -> None:
        self.error_seen = True


class _EnvelopeProbeCollector(ProbeMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.error_seen = False
        self.value_seen = None

    def read_metric_input(self):
        return ReadEnvelope(ok=False, value={"probe": True})

    def collect_hook(self, registry, metric_input) -> None:
        self.value_seen = metric_input

    def collect_stale_hook(self, registry, error: Exception) -> None:
        self.error_seen = True


def test_metric_datasource_owns_safe_read_helpers_and_datasource_subclasses_reuse_them():
    project_root = Path(__file__).resolve().parents[3]

    core_base = (project_root / "openviking" / "metrics" / "core" / "base.py").read_text(
        encoding="utf-8"
    )
    datasource_base = (
        project_root / "openviking" / "metrics" / "datasources" / "base.py"
    ).read_text(encoding="utf-8")
    probes_text = (project_root / "openviking" / "metrics" / "datasources" / "probes.py").read_text(
        encoding="utf-8"
    )
    encryption = (
        project_root / "openviking" / "metrics" / "datasources" / "encryption.py"
    ).read_text(encoding="utf-8")
    model_usage = (
        project_root / "openviking" / "metrics" / "datasources" / "model_usage.py"
    ).read_text(encoding="utf-8")
    observer_state = (
        project_root / "openviking" / "metrics" / "datasources" / "observer_state.py"
    ).read_text(encoding="utf-8")

    assert "def safe_read(" in core_base
    assert "def safe_read_async(" in core_base
    assert "def best_effort(" not in datasource_base
    assert "def best_effort_async(" not in datasource_base

    assert "safe_value_probe(" in datasource_base
    assert probes_text.count("safe_value_probe(") >= 1
    assert encryption.count("safe_value_probe(") >= 1

    assert '"available"' in model_usage
    assert '"usage_by_model"' in model_usage
    assert ".as_dict(" in model_usage
    assert ".normalize_str(" in model_usage
    assert ".as_dict(" in observer_state or ".normalize_str(" in observer_state


def test_state_collector_unwraps_envelope_and_routes_ok_false_to_stale_hook():
    registry = MetricRegistry()
    collector = _EnvelopeStateCollector()

    collector.collect(registry)

    assert collector.error_seen is True
    assert collector.value_seen is None


def test_probe_collector_unwraps_envelope_and_routes_ok_false_to_stale_hook():
    registry = MetricRegistry()
    collector = _EnvelopeProbeCollector()

    collector.collect(registry)

    assert collector.error_seen is True
    assert collector.value_seen is None


def test_service_probe_datasource_reads_service_and_app_state_success():
    app = SimpleNamespace(state=SimpleNamespace(api_key_manager=object()))
    service = SimpleNamespace(initialized=True)
    ds = probes.ServiceProbeDataSource(app=app, service=service)

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"service_readiness": True, "api_key_manager_readiness": True}
    assert env.error_type is None


def test_service_probe_datasource_returns_default_on_exception():
    class _BadApp:
        @property
        def state(self):
            raise RuntimeError("boom")

    ds = probes.ServiceProbeDataSource(app=_BadApp(), service=SimpleNamespace(initialized=True))

    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"service_readiness": False, "api_key_manager_readiness": False}
    assert env.error_type == "RuntimeError"
    assert "boom" in (env.error_message or "")


def test_storage_probe_datasource_returns_agfs_probe_success(monkeypatch):
    monkeypatch.setattr(probes, "get_viking_fs", lambda: SimpleNamespace(agfs=object()))
    ds = probes.StorageProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"agfs": True}


def test_storage_probe_datasource_returns_default_on_exception(monkeypatch):
    def _boom():
        raise RuntimeError("no fs")

    monkeypatch.setattr(probes, "get_viking_fs", _boom)
    ds = probes.StorageProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"agfs": False}
    assert env.error_type == "RuntimeError"


def test_retrieval_backend_probe_datasource_returns_false_when_no_service():
    ds = probes.RetrievalBackendProbeDataSource(service=None)
    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"vikingdb": False}


def test_retrieval_backend_probe_datasource_returns_default_on_exception(monkeypatch):
    class _VikingDB:
        def health_check(self):
            return object()

    service = SimpleNamespace(vikingdb=_VikingDB())
    ds = probes.RetrievalBackendProbeDataSource(service=service)

    def _boom(_coro):
        raise RuntimeError("runner failed")

    monkeypatch.setattr(probes, "run_async", _boom)
    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"vikingdb": False}
    assert env.error_type == "RuntimeError"


def test_model_provider_probe_datasource_returns_provider_tuple_success():
    class _VlmCfg:
        provider = "volcengine"

        def get_vlm_instance(self):
            return object()

    cfg = SimpleNamespace(vlm=_VlmCfg())
    ds = probes.ModelProviderProbeDataSource(config_provider=lambda: cfg)

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"provider": ("volcengine", True)}


def test_model_provider_probe_datasource_returns_default_on_exception():
    class _BadVlmCfg:
        provider = "volcengine"

        def get_vlm_instance(self):
            raise RuntimeError("bad cfg")

    cfg = SimpleNamespace(vlm=_BadVlmCfg())
    ds = probes.ModelProviderProbeDataSource(config_provider=lambda: cfg)

    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"provider": ("unknown", False)}
    assert env.error_type == "RuntimeError"


def test_async_system_probe_datasource_returns_queue_probe_success(monkeypatch):
    monkeypatch.setattr(probes, "get_queue_manager", lambda: object())
    ds = probes.AsyncSystemProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"queue": True}


def test_async_system_probe_datasource_returns_default_on_exception(monkeypatch):
    def _boom():
        raise RuntimeError("no queue")

    monkeypatch.setattr(probes, "get_queue_manager", _boom)
    ds = probes.AsyncSystemProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"queue": False}
    assert env.error_type == "RuntimeError"


def test_vikingdb_state_datasource_uses_default_account_ctx_for_count(monkeypatch):
    captured: dict[str, object] = {}

    class DummyVikingDB:
        collection_name = "my_collection"

        async def health_check(self):
            return True

        async def count(self, filter=None, ctx=None):
            captured["ctx"] = ctx
            return 123

    class Service:
        _vikingdb_manager = DummyVikingDB()

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(default_account="acct_demo", default_user="user_demo"),
    )

    env = VikingDBStateDataSource(service=Service()).read_vikingdb_state()
    assert env.ok is True
    assert env.value == [("acct_demo", "my_collection", True, 123)]
    ctx = captured["ctx"]
    assert getattr(ctx, "account_id", None) == "acct_demo"


def test_vikingdb_state_datasource_fanout_reads_multiple_accounts(monkeypatch):
    seen: list[tuple[str, str]] = []

    class DummyVikingDB:
        collection_name = "context"

        async def health_check(self):
            return True

        async def count(self, filter=None, ctx=None):
            seen.append(
                (
                    getattr(ctx, "account_id", ""),
                    getattr(getattr(ctx, "user", None), "user_id", ""),
                )
            )
            return {"acct_default": 11, "acct_a": 21, "acct_b": 31}.get(
                getattr(ctx, "account_id", ""), 0
            )

    class APIKeyManager:
        def get_accounts(self):
            return [{"account_id": "acct_a"}, {"account_id": "acct_b"}]

        def get_users(self, account_id, limit=1, expose_key=False):
            return [{"user_id": f"user_{account_id[-1]}"}]

    class App:
        state = SimpleNamespace(api_key_manager=APIKeyManager())

    class Service:
        _vikingdb_manager = DummyVikingDB()

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(default_account="acct_default", default_user="user_default"),
    )

    env = VikingDBStateDataSource(service=Service(), app=App()).read_vikingdb_state()
    assert env.ok is True
    assert env.value == [
        ("acct_default", "context", True, 11),
        ("acct_a", "context", True, 21),
        ("acct_b", "context", True, 31),
    ]
    assert seen == [
        ("acct_default", "user_default"),
        ("acct_a", "user_a"),
        ("acct_b", "user_b"),
    ]


def test_vikingdb_state_datasource_fanout_partial_failure_marks_failed_account_not_ok(monkeypatch):
    health_calls = {"count": 0}

    class DummyVikingDB:
        collection_name = "context"

        async def health_check(self):
            health_calls["count"] += 1
            return True

        async def count(self, filter=None, ctx=None):
            if getattr(ctx, "account_id", "") == "acct_b":
                raise RuntimeError("acct_b down")
            return {"acct_default": 11, "acct_a": 21}.get(getattr(ctx, "account_id", ""), 0)

    class APIKeyManager:
        def get_accounts(self):
            return [{"account_id": "acct_a"}, {"account_id": "acct_b"}]

        def get_users(self, account_id, limit=1, expose_key=False):
            return [{"user_id": f"user_{account_id[-1]}"}]

    class App:
        state = SimpleNamespace(api_key_manager=APIKeyManager())

    class Service:
        _vikingdb_manager = DummyVikingDB()

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(default_account="acct_default", default_user="user_default"),
    )

    env = VikingDBStateDataSource(service=Service(), app=App()).read_vikingdb_state()
    assert env.ok is True
    assert env.value == [
        ("acct_default", "context", True, 11),
        ("acct_a", "context", True, 21),
        ("acct_b", "context", False, 0),
    ]
    assert env.error_type == "RuntimeError"
    assert "acct_b down" in (env.error_message or "")
    assert health_calls["count"] == 1


def test_vikingdb_state_datasource_fanout_all_fail_sets_envelope_not_ok(monkeypatch):
    class DummyVikingDB:
        collection_name = "context"

        async def health_check(self):
            raise RuntimeError("health down")

        async def count(self, filter=None, ctx=None):
            raise RuntimeError("count down")

    class Service:
        _vikingdb_manager = DummyVikingDB()

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(default_account="acct_default", default_user="user_default"),
    )

    env = VikingDBStateDataSource(service=Service()).read_vikingdb_state()
    assert env.ok is False
    assert env.value == [("acct_default", "context", False, 0)]
    assert env.error_type == "RuntimeError"
