# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking_cli.utils.config import storage_config as storage_config_module
from openviking_cli.utils.config.storage_config import StorageConfig


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


def test_storage_config_builds_persistent_task_tracker():
    tracker = StorageConfig().build_task_tracker(_FakeAgfs())
    assert isinstance(tracker, TaskTracker)
    assert isinstance(tracker._store, PersistentTaskStore)


def test_storage_config_passes_service_name_to_mysql_task_store(monkeypatch):
    from openviking.service import task_store_mysql

    created = []

    class _FakeMySQLTaskWorkStore:
        def __init__(self, *, params, service_name):
            created.append((params, service_name))

    monkeypatch.setattr(task_store_mysql, "MySQLTaskWorkStore", _FakeMySQLTaskWorkStore)

    tracker = StorageConfig.model_validate(
        {
            "task_store": {
                "provider": "mysql",
                "params": {"user": "u", "password": "p", "database": "db"},
            }
        }
    ).build_task_tracker(_FakeAgfs(), service_name="tenant-a")

    assert isinstance(tracker, TaskTracker)
    assert tracker._store.__class__ is _FakeMySQLTaskWorkStore
    assert created == [({"user": "u", "password": "p", "database": "db"}, "tenant-a")]


def test_storage_config_allows_mysql_task_store_params_service_name(monkeypatch):
    from openviking.service import task_store_mysql

    created = []

    class _FakeMySQLTaskWorkStore:
        def __init__(self, *, params, service_name):
            created.append((params, service_name))

    monkeypatch.setattr(task_store_mysql, "MySQLTaskWorkStore", _FakeMySQLTaskWorkStore)

    StorageConfig.model_validate(
        {
            "task_store": {
                "provider": "mysql",
                "params": {
                    "user": "u",
                    "password": "p",
                    "database": "db",
                    "service_name": "tenant-from-params",
                },
            },
        }
    ).build_task_tracker(_FakeAgfs())

    assert created == [
        (
            {
                "user": "u",
                "password": "p",
                "database": "db",
                "service_name": "tenant-from-params",
            },
            None,
        )
    ]
