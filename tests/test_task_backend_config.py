# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.service.task_store import InMemoryTaskStore, PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
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


def test_storage_config_defaults_task_backend_to_memory():
    config = StorageConfig()
    assert config.task_tracker.backend == "memory"


def test_storage_config_accepts_memory_task_backend():
    config = StorageConfig(task_tracker={"backend": "memory"})
    assert config.task_tracker.backend == "memory"


def test_storage_config_builds_memory_task_tracker():
    tracker = StorageConfig(task_tracker={"backend": "memory"}).build_task_tracker(_FakeAgfs())
    assert isinstance(tracker, TaskTracker)
    assert isinstance(tracker._store, InMemoryTaskStore)


def test_storage_config_builds_persistent_task_tracker():
    tracker = StorageConfig(task_tracker={"backend": "persistent"}).build_task_tracker(_FakeAgfs())
    assert isinstance(tracker, TaskTracker)
    assert isinstance(tracker._store, PersistentTaskStore)
