# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for OpenVikingService encryption startup wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.service.core import OpenVikingService
from openviking.utils.agfs_utils import RagfsBindingConfig


class _FakeConfig:
    """Minimal config object exposing the service-facing to_dict API."""

    storage = SimpleNamespace(
        agfs=SimpleNamespace(path="/tmp/ov-test", backend="local"),
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

    monkeypatch.setattr("openviking.service.core.bootstrap_encryption", _bootstrap)
    service = OpenVikingService.__new__(OpenVikingService)
    service._config = _FakeConfig()
    service._encryptor = None

    ragfs_config = service._build_ragfs_binding_config()

    assert isinstance(ragfs_config, RagfsBindingConfig)
    assert ragfs_config.agfs is service._config.storage.agfs
    assert ragfs_config.to_binding_dict() == {
        "encryption": {
            "root_key": b"k" * 32,
            "provider_type": 1,
        }
    }
    assert isinstance(service._encryptor, _FakeEncryptor)


@pytest.mark.parametrize(
    ("encrypted_mode", "raw", "message"),
    [
        (True, b"{}", "plaintext"),
        (False, b"OVE1ciphertext", "encrypted"),
    ],
)
def test_probe_storage_shape_rejects_mode_mismatch(encrypted_mode, raw, message):
    """Reject existing system metadata whose shape differs from current encryption mode."""

    class _Client:
        def read_raw(self, path: str) -> bytes:
            assert path == "/local/_system/accounts.json"
            return raw

    service = OpenVikingService.__new__(OpenVikingService)

    with pytest.raises(RuntimeError, match=message):
        service._probe_storage_shape(_Client(), encrypted_mode)


def test_probe_storage_shape_allows_empty_system():
    """Treat missing system metadata as a fresh system."""

    class _Client:
        def read_raw(self, path: str) -> bytes:
            assert path == "/local/_system/accounts.json"
            raise AGFSNotFoundError("not found")

    service = OpenVikingService.__new__(OpenVikingService)

    service._probe_storage_shape(_Client(), encrypted_mode=True)


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
