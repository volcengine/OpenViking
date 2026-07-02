# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for OpenVikingService encryption startup wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.server.config import SessionAutoCommitConfig
from openviking.service.core import OpenVikingService
from openviking.utils.agfs_utils import RagfsBindingConfig


class _FakeCacheConfig:
    def model_dump(self, mode: str) -> dict:
        assert mode == "json"
        return {"enabled": False, "provider": "memory"}


class _FakeConfig:
    """Minimal config object exposing the service-facing to_dict API."""

    storage = SimpleNamespace(
        agfs=SimpleNamespace(
            path="/tmp/ov-test",
            backend="local",
            cache=_FakeCacheConfig(),
        ),
        skip_process_lock=False,
    )

    def to_dict(self) -> dict:
        return {"encryption": {"enabled": True, "provider": "local"}}


class _FakeProvider:
    """Fake provider with async root-key retrieval."""

    async def get_root_key(self) -> bytes:
        return b"k" * 32


class _FakeEncryptor:
    """Fake encryptor returned by bootstrap_encryption."""

    provider_type = 1

    def __init__(self) -> None:
        self.provider = _FakeProvider()


@pytest.mark.asyncio
async def test_build_ragfs_binding_config_works_inside_running_event_loop(monkeypatch):
    """Build the single binding config from async bootstrap while already in an event loop."""

    async def _bootstrap(config: dict) -> _FakeEncryptor:
        assert config["encryption"]["enabled"] is True
        return _FakeEncryptor()

    monkeypatch.setattr("openviking.crypto.config.bootstrap_encryption", _bootstrap)
    service = OpenVikingService.__new__(OpenVikingService)
    service._config = _FakeConfig()
    service._encryptor = None

    ragfs_config = service._build_ragfs_binding_config()

    assert isinstance(ragfs_config, RagfsBindingConfig)
    assert ragfs_config.agfs is service._config.storage.agfs
    assert ragfs_config.to_binding_dict() == {
        "cache": {
            "enabled": False,
            "provider": "memory",
        },
        "encryption": {
            "root_key": b"k" * 32,
            "provider_type": 1,
        },
    }
    assert isinstance(service._encryptor, _FakeEncryptor)


def test_ensure_data_dir_lock_acquired_once(monkeypatch, tmp_path):
    """Acquire the data-dir lock once before startup encryption bootstrap."""

    calls = []

    def _acquire(path: str) -> str:
        calls.append(path)
        return str(tmp_path / ".openviking.pid")

    monkeypatch.setattr("openviking.utils.process_lock.acquire_data_dir_lock", _acquire)
    service = OpenVikingService.__new__(OpenVikingService)
    service._config = SimpleNamespace(
        storage=SimpleNamespace(workspace=str(tmp_path), skip_process_lock=False)
    )
    service._data_dir_lock_acquired = False

    service._ensure_data_dir_lock_acquired()
    service._ensure_data_dir_lock_acquired()

    assert calls == [str(tmp_path)]


def test_ensure_data_dir_lock_respects_skip_process_lock(monkeypatch, tmp_path):
    """Skip lock acquisition entirely when storage.skip_process_lock is enabled."""

    calls = []

    def _acquire(path: str) -> str:
        calls.append(path)
        return str(tmp_path / ".openviking.pid")

    monkeypatch.setattr("openviking.utils.process_lock.acquire_data_dir_lock", _acquire)
    service = OpenVikingService.__new__(OpenVikingService)
    service._config = SimpleNamespace(
        storage=SimpleNamespace(workspace=str(tmp_path), skip_process_lock=True)
    )
    service._data_dir_lock_acquired = False

    service._ensure_data_dir_lock_acquired()

    assert calls == []
    assert service._data_dir_lock_acquired is True


def test_session_auto_commit_config_defaults_to_idle_disabled():
    config = SessionAutoCommitConfig()

    assert config.idle_enabled is False
    assert config.check_interval_seconds == 60.0
    assert config.scan_batch_size == 16
    assert config.scan_batch_pause_seconds == 0.0


def test_session_auto_commit_config_accepts_check_interval_override():
    config = SessionAutoCommitConfig(
        idle_enabled=True,
        check_interval_seconds=3.5,
        scan_batch_size=8,
        scan_batch_pause_seconds=0.2,
    )

    assert config.idle_enabled is True
    assert config.check_interval_seconds == 3.5
    assert config.scan_batch_size == 8
    assert config.scan_batch_pause_seconds == 0.2


@pytest.mark.asyncio
async def test_initialize_skips_session_auto_commit_scheduler_when_idle_disabled(monkeypatch):
    """Do not create or start the idle scheduler when idle auto-commit is globally disabled."""

    scheduler_events: list[str] = []

    async def _fake_init_context_collection(*_args, **_kwargs):
        return None

    class _FakeWatchScheduler:
        def __init__(self, resource_service, viking_fs):
            self.resource_service = resource_service
            self.viking_fs = viking_fs

        async def start(self):
            return None

    class _FakeSessionAutoCommitScheduler:
        def __init__(self, *args, **kwargs):
            scheduler_events.append("init")
            self.index = object()

        async def start(self):
            scheduler_events.append("start")

    class _FakeDirectoryInitializer:
        def __init__(self, vikingdb, viking_fs):
            self.vikingdb = vikingdb
            self.viking_fs = viking_fs

        async def initialize_account_directories(self, _ctx):
            return 0

        async def initialize_user_directories(self, _ctx):
            return 0

    class _FakeQueueManager:
        EXTERNAL_PARSE = "external_parse"

        def get_queue(self, *_args, **_kwargs):
            return object()

        def start(self):
            return None

    class _FakeLockManager:
        async def start(self):
            return None

    class _FakeVikingDBManager:
        def mark_closing(self):
            return None

    monkeypatch.setattr(
        "openviking.service.core.init_context_collection",
        _fake_init_context_collection,
    )
    monkeypatch.setattr("openviking.service.core.init_viking_fs", lambda **_kwargs: object())
    monkeypatch.setattr("openviking.service.core.DirectoryInitializer", _FakeDirectoryInitializer)
    monkeypatch.setattr("openviking.service.core.WatchScheduler", _FakeWatchScheduler)
    monkeypatch.setattr(
        "openviking.service.core.SessionAutoCommitScheduler",
        _FakeSessionAutoCommitScheduler,
    )
    monkeypatch.setattr(
        "openviking.service.core.create_session_compressor",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr("openviking.service.core.ResourceProcessor", lambda **_kwargs: object())
    monkeypatch.setattr("openviking.service.core.SkillProcessor", lambda **_kwargs: object())
    monkeypatch.setattr(
        "openviking.service.core.get_openviking_config",
        lambda: SimpleNamespace(rerank=object(), retrieval=object(), grep=object()),
    )
    monkeypatch.setattr(
        "openviking.server.dependencies.get_server_config",
        lambda: SimpleNamespace(session_auto_commit=SessionAutoCommitConfig(idle_enabled=False)),
    )

    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = False
    service._data_dir_lock_acquired = True
    service._config = SimpleNamespace(
        embedding=SimpleNamespace(
            max_concurrent=1,
            dimension=1024,
            get_embedder=lambda: SimpleNamespace(is_sparse=False),
        ),
        vlm=SimpleNamespace(max_concurrent=1),
        storage=SimpleNamespace(skip_process_lock=True),
    )
    service._user = SimpleNamespace()
    service._encryptor = None
    service._agfs_client = object()
    service._queue_manager = _FakeQueueManager()
    service._vikingdb_manager = _FakeVikingDBManager()
    service._viking_fs = None
    service._embedder = object()
    service._resource_processor = None
    service._skill_processor = None
    service._session_compressor = None
    service._lock_manager = _FakeLockManager()
    service._directory_initializer = None
    service._watch_scheduler = None
    service._session_auto_commit_scheduler = None
    service._privacy_config_service = None
    service._fs_service = SimpleNamespace(set_dependencies=lambda **_kwargs: None)
    service._relation_service = SimpleNamespace(set_viking_fs=lambda _fs: None)
    service._pack_service = SimpleNamespace(set_dependencies=lambda **_kwargs: None)
    service._search_service = SimpleNamespace(set_viking_fs=lambda _fs: None)
    service._resource_memory_link_service = SimpleNamespace(set_dependencies=lambda **_kwargs: None)
    service._resource_service = SimpleNamespace(
        set_dependencies=lambda **_kwargs: None,
        close_background_tasks=lambda: None,
    )
    service._session_service = SimpleNamespace(
        set_dependencies=lambda **_kwargs: None,
        set_session_auto_commit_config=lambda config: setattr(
            service, "_captured_session_auto_commit_config", config
        ),
    )
    service._debug_service = SimpleNamespace(set_dependencies=lambda **_kwargs: None)
    service._init_storage = lambda *_args, **_kwargs: None
    service._build_ragfs_binding_config = lambda: None
    service._ensure_data_dir_lock_acquired = lambda: None

    await service.initialize()

    assert scheduler_events == []
    assert service._session_auto_commit_scheduler is None
    assert service._captured_session_auto_commit_config.idle_enabled is False
