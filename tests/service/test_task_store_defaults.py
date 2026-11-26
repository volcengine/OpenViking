# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default backend selection recognizes legacy File task directories."""

from pathlib import Path

import pytest

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.service.task_domain import TaskRecord
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
from openviking_cli.utils.config.storage_config import StorageConfig, TaskStoreConfig


class LocalListing:
    def __init__(self, root):
        self.root = root
        self.calls = []

    def ls(self, path, ctx=None):
        assert path == "/local", "Task detection must only list accounts"
        self.calls.append(("ls", path, ctx))
        directory = self.root / path.removeprefix("/local").lstrip("/")
        return [
            {"name": entry.name, "isDir": entry.is_dir()} for entry in sorted(directory.iterdir())
        ]

    def stat(self, path, ctx=None):
        self.calls.append(("stat", path, ctx))
        target = self.root / path.removeprefix("/local").lstrip("/")
        target.stat()
        return {"name": target.name, "isDir": target.is_dir()}


@pytest.mark.parametrize(
    "task_path",
    [None, "acme/_system/tasks", "acme/_system/tasks/alice", "_system/tasks", "_system/tasks/root"],
)
@pytest.mark.parametrize("has_record", [False, True])
def test_default_backend_uses_task_directories(tmp_path, task_path, has_record):
    root = tmp_path / "viking"
    root.mkdir()
    if task_path is not None:
        directory = root / task_path
        directory.mkdir(parents=True)
        (directory / ".redirect.json").write_text("{}")
        (directory / "directory.json").mkdir()
        if has_record:
            # Even malformed records identify an existing File store.
            (directory / "task.json").write_text("malformed")
    config = StorageConfig(workspace=str(tmp_path))
    agfs = LocalListing(root)
    tracker = config.build_task_tracker(agfs)
    expected = PersistentTaskStore if task_path else SQLiteTaskWorkStore
    assert isinstance(tracker._store, expected)
    assert config.task_store.provider == "file"
    assert "provider" not in config.task_store.model_fields_set
    for _, path, ctx in agfs.calls:
        expected_account = path.split("/")[2] if path != "/local" else "_system"
        assert ctx == {"account_id": expected_account}


@pytest.mark.parametrize("provider", ["file", "sqlite", "mysql", "postgres"])
def test_explicit_backend_skips_detection(tmp_path, monkeypatch, provider):
    from openviking.service import task_store_mysql, task_store_postgres

    def unexpected_detection(*args):
        pytest.fail("Explicit provider must not probe File task storage")

    class FakeSQLStore:
        def __init__(self, **kwargs):
            self.params = kwargs["params"]

    monkeypatch.setattr(PersistentTaskStore, "has_task_store", unexpected_detection)
    monkeypatch.setattr(task_store_mysql, "MySQLTaskWorkStore", FakeSQLStore)
    monkeypatch.setattr(task_store_postgres, "PostgresTaskWorkStore", FakeSQLStore)
    config = StorageConfig(
        workspace=str(tmp_path), task_store={"provider": f" {provider.upper()} "}
    )
    assert config.task_store.provider == provider
    assert config.task_store.model_fields_set == {"provider"}
    store = config.build_task_tracker(None)._store
    expected = {
        "file": PersistentTaskStore,
        "sqlite": SQLiteTaskWorkStore,
        "mysql": FakeSQLStore,
        "postgres": FakeSQLStore,
    }
    assert isinstance(store, expected[provider])


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("storage unavailable")])
@pytest.mark.parametrize("operation", ["system_stat", "account_ls", "account_stat"])
def test_detection_failure_does_not_select_sqlite(tmp_path, error, operation):
    class BrokenListing(LocalListing):
        def ls(self, path, ctx=None):
            if operation == "account_ls":
                raise error
            return super().ls(path, ctx)

        def stat(self, path, ctx=None):
            if operation == "system_stat" or (
                operation == "account_stat" and path != "/local/_system/tasks"
            ):
                raise error
            return super().stat(path, ctx)

    (tmp_path / "acme").mkdir()
    with pytest.raises(type(error), match=str(error)):
        StorageConfig(workspace=str(tmp_path)).build_task_tracker(BrokenListing(tmp_path))


def test_system_task_directory_short_circuits_before_listing_accounts(tmp_path):
    (tmp_path / "_system/tasks").mkdir(parents=True)
    agfs = LocalListing(tmp_path)
    assert PersistentTaskStore.has_task_store(agfs)
    assert agfs.calls == [("stat", "/local/_system/tasks", {"account_id": "_system"})]


def test_account_probe_stops_at_first_task_root(tmp_path):
    (tmp_path / "_system").mkdir()
    (tmp_path / "acme/_system/tasks").mkdir(parents=True)
    (tmp_path / "later/_system/tasks").mkdir(parents=True)
    agfs = LocalListing(tmp_path)
    assert PersistentTaskStore.has_task_store(agfs)
    assert agfs.calls == [
        ("stat", "/local/_system/tasks", {"account_id": "_system"}),
        ("ls", "/local", {"account_id": "_system"}),
        ("stat", "/local/acme/_system/tasks", {"account_id": "acme"}),
    ]


def test_task_root_must_be_directory(tmp_path):
    (tmp_path / "_system").mkdir()
    (tmp_path / "_system/tasks").write_text("not a directory")
    (tmp_path / "acme/_system").mkdir(parents=True)
    (tmp_path / "acme/_system/tasks").write_text("not a directory")
    (tmp_path / "unrelated-file").write_text("not an account")
    assert not PersistentTaskStore.has_task_store(LocalListing(tmp_path))


def test_native_not_found_is_treated_as_no_file_store():
    class MissingStorage:
        def stat(self, path, ctx=None):
            raise AGFSNotFoundError("missing task root")

        def ls(self, path, ctx=None):
            raise AGFSNotFoundError("missing storage root")

    assert not PersistentTaskStore.has_task_store(MissingStorage())


def test_missing_root_and_normalization(tmp_path):
    assert TaskStoreConfig().model_fields_set == set()
    assert TaskStoreConfig(provider=" FILE ").model_fields_set == {"provider"}
    with pytest.raises(ValueError, match="storage.task_store.provider"):
        TaskStoreConfig(provider="unknown")
    store = (
        StorageConfig(workspace=str(tmp_path))
        .build_task_tracker(LocalListing(tmp_path / "absent"))
        ._store
    )
    assert isinstance(store, SQLiteTaskWorkStore)
    assert Path(store._url.database) == tmp_path / "task_store/tasks.db"


async def test_sqlite_restart_stays_sqlite(tmp_path):
    agfs = LocalListing(tmp_path / "viking")
    config = StorageConfig(workspace=str(tmp_path))
    first = config.build_task_tracker(agfs)._store
    await first.load()
    try:
        await first.create(
            TaskRecord(task_id="retained", task_type="compile", account_id="acme", user_id="alice")
        )
    finally:
        await first.close()
    second = config.build_task_tracker(agfs)._store
    assert isinstance(second, SQLiteTaskWorkStore)
    await second.load()
    try:
        assert await second.get_task("retained", account_id="acme", user_id="alice") is not None
    finally:
        await second.close()
