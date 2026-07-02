# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking_cli.utils.config import storage_config as storage_config_module
from openviking_cli.utils.config.storage_config import DiskPressureSafetyConfig, StorageConfig


class _FakeAgfs:
    def mkdir(self, path: str, mode: str = "755"):
        return {"message": "created", "mode": mode}

    def write(self, path: str, data):
        return "OK"

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        raise FileNotFoundError(path)

    def ls(self, path: str = "/"):
        return []

    def rm(self, path: str, recursive: bool = False, force: bool = True):
        return {"message": "deleted"}


def test_storage_config_has_no_task_tracker_config():
    config = StorageConfig()
    assert not hasattr(config, "task_tracker")


def test_storage_config_ignores_deprecated_task_tracker_config(monkeypatch):
    warnings = []
    monkeypatch.setattr(storage_config_module.logger, "warning", warnings.append)

    config = StorageConfig(task_tracker={"backend": "memory"})

    assert not hasattr(config, "task_tracker")
    assert any("task_tracker" in message for message in warnings)
    assert any("deprecated and ignored" in message for message in warnings)


def test_storage_config_defaults_skip_process_lock_to_false():
    config = StorageConfig()
    assert config.skip_process_lock is False


def test_storage_config_accepts_skip_process_lock():
    config = StorageConfig(skip_process_lock=True)
    assert config.skip_process_lock is True


def test_storage_safety_defaults_are_disabled():
    config = StorageConfig()
    assert config.safety.resource_retention.enabled is False
    assert config.safety.disk_pressure.enabled is False
    assert config.safety.disk_pressure.warning_threshold_percent == 85
    assert config.safety.disk_pressure.critical_threshold_percent == 95
    assert config.safety.disk_pressure.action_on_critical == "block_writes"


def test_disk_pressure_rejects_critical_below_warning():
    with pytest.raises(ValueError, match="critical_threshold_percent"):
        DiskPressureSafetyConfig(
            warning_threshold_percent=90,
            critical_threshold_percent=80,
        )


def test_storage_config_builds_persistent_task_tracker():
    tracker = StorageConfig().build_task_tracker(_FakeAgfs())
    assert isinstance(tracker, TaskTracker)
    assert isinstance(tracker._store, PersistentTaskStore)
