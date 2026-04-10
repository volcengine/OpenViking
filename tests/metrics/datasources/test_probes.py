"""Module-level tests for `openviking.metrics.datasources.probes`."""

from __future__ import annotations

from types import SimpleNamespace

import openviking.metrics.datasources.probes as probes


def test_service_probe_datasource_reads_service_and_app_state_success():
    """ServiceProbeDataSource returns readiness booleans derived from service/app state."""

    app = SimpleNamespace(state=SimpleNamespace(api_key_manager=object()))
    service = SimpleNamespace(initialized=True)
    ds = probes.ServiceProbeDataSource(app=app, service=service)

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"service_readiness": True, "api_key_manager_readiness": True}
    assert env.error_type is None


def test_service_probe_datasource_returns_default_on_exception():
    """ServiceProbeDataSource must wrap exceptions into `ok=False` with deterministic default."""

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
    """StorageProbeDataSource exposes a single `agfs` boolean probe on success."""

    monkeypatch.setattr(probes, "get_viking_fs", lambda: SimpleNamespace(agfs=object()))
    ds = probes.StorageProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"agfs": True}


def test_storage_probe_datasource_returns_default_on_exception(monkeypatch):
    """StorageProbeDataSource must return `ok=False` with `{agfs: False}` on read failure."""

    def _boom():
        raise RuntimeError("no fs")

    monkeypatch.setattr(probes, "get_viking_fs", _boom)
    ds = probes.StorageProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"agfs": False}
    assert env.error_type == "RuntimeError"


def test_retrieval_backend_probe_datasource_returns_false_when_no_service():
    """RetrievalBackendProbeDataSource treats missing wiring as a negative readiness (ok=True)."""

    ds = probes.RetrievalBackendProbeDataSource(service=None)
    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"vikingdb": False}


def test_retrieval_backend_probe_datasource_returns_default_on_exception(monkeypatch):
    """RetrievalBackendProbeDataSource must return `ok=False` on health-check runner failures."""

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
    """ModelProviderProbeDataSource must return provider name and a boolean ok flag."""

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
    """ModelProviderProbeDataSource must fall back to `unknown/False` on config failures."""

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
    """AsyncSystemProbeDataSource returns a boolean `queue` probe based on queue manager wiring."""

    monkeypatch.setattr(probes, "get_queue_manager", lambda: object())
    ds = probes.AsyncSystemProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == {"queue": True}


def test_async_system_probe_datasource_returns_default_on_exception(monkeypatch):
    """AsyncSystemProbeDataSource must return `ok=False` with `{queue: False}` on failures."""

    def _boom():
        raise RuntimeError("no queue")

    monkeypatch.setattr(probes, "get_queue_manager", _boom)
    ds = probes.AsyncSystemProbeDataSource()

    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == {"queue": False}
    assert env.error_type == "RuntimeError"
