# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Offline migration preserves File tasks and recovers queued work after restart."""

import json
import sqlite3
import subprocess
import sys

import pytest

from openviking.crypto.config import bootstrap_encryption
from openviking.service.task_domain import TaskRecord, TaskStatus, WorkState
from openviking.service.task_events import append_task_event
from openviking.service.task_store import PersistentTaskStore, _task_to_payload
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
from openviking.utils.process_lock import acquire_data_dir_lock, release_data_dir_lock
from scripts.maintenance.task_store.migrate_file_to_sqlite import migrate

OWNER = {"account_id": "acme", "user_id": "alice"}


@pytest.mark.parametrize("source_status", ["pending", "processing"])
async def test_real_ragfs_file_and_queue_migrate(tmp_path, monkeypatch, source_status):
    from openviking.service import task_tracker as tracker_module
    from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
    from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
    from openviking.storage.queuefs.process_result import ProcessResult
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
    from openviking_cli.utils.config.agfs_config import AGFSConfig
    from openviking_cli.utils.config.storage_config import StorageConfig

    binding = RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    task = TaskRecord(task_id="native-task", task_type="compile", **OWNER)
    client = create_agfs_client(binding)
    try:
        assert isinstance(
            StorageConfig(workspace=str(tmp_path)).build_task_tracker(client)._store,
            SQLiteTaskWorkStore,
        )
        await PersistentTaskStore(client).create(task)
        assert isinstance(
            StorageConfig(workspace=str(tmp_path)).build_task_tracker(client)._store,
            PersistentTaskStore,
        )
        queue = NamedQueue(client, "/queue", "MigrationTest")
        await queue.enqueue({"task_id": task.task_id, "_task_work_id": "native-work", **OWNER})
        [original] = await queue.snapshot()
        message_id = original["id"]
        if source_status == "processing":
            message = await queue.dequeue_raw()
            assert message is not None and message["id"] == message_id
    finally:
        client.close()

    queue_db = tmp_path / "_system/queue/queue.db"
    with sqlite3.connect(queue_db) as connection:
        assert connection.execute(
            "SELECT status FROM queue_messages WHERE message_id=?", (message_id,)
        ).fetchone() == (source_status,)

    result = await migrate(
        workspace=tmp_path, config={}, sqlite_path=tmp_path / "task_store/tasks.db", execute=True
    )
    assert result.failed == 0 and result.tasks_inserted == 1 and result.works_inserted == 1

    tracker = StorageConfig(
        workspace=str(tmp_path), task_store={"provider": "sqlite"}
    ).build_task_tracker(None)
    await tracker.load()
    client = create_agfs_client(binding)
    monkeypatch.setattr(tracker_module, "get_task_tracker", lambda: tracker)
    handled = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            assert (
                await tracker._store.get_work(task.task_id, "native-work", **OWNER)
            ).state == WorkState.RUNNING
            handled.append(data["id"])
            await tracker.complete(task.task_id, {"migrated": True}, **OWNER)
            return ProcessResult.success()

    try:
        queue = NamedQueue(
            client,
            "/queue",
            "MigrationTest",
            dequeue_handler=Handler(),
            middlewares=[TaskWorkQueueMiddleware()],
        )
        recovered = await queue.dequeue_raw()
        assert recovered is not None and recovered["id"] == message_id
        assert (
            await tracker._store.get_work(task.task_id, "native-work", **OWNER)
        ).state == WorkState.PENDING
        result = await queue.process_dequeued(recovered)
        await queue.settle_dequeued(recovered, result)
        assert handled == [message_id]
        assert (
            await tracker._store.get_work(task.task_id, "native-work", **OWNER)
        ).state == WorkState.DONE
        assert (await tracker.get(task.task_id, **OWNER)).status == TaskStatus.COMPLETED
        assert (await queue.get_status()).is_complete
        assert await queue.snapshot() == []
    finally:
        client.close()
        await tracker.close()

    with sqlite3.connect(queue_db) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM queue_messages WHERE message_id=?", (message_id,)
            ).fetchone()[0]
            == 0
        )
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "task_store/tasks.db")})
    await store.load()
    try:
        assert (await store.get_work(task.task_id, "native-work", **OWNER)).state == WorkState.DONE
        assert (await store.get_task(task.task_id, **OWNER)).status == TaskStatus.COMPLETED
    finally:
        await store.close()


def write_task(root, task):
    directory = (
        root / "_system/tasks"
        if task.account_id == "_system"
        else root / task.account_id / "_system/tasks"
    ) / task.user_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task.task_id}.json"
    path.write_text(json.dumps(_task_to_payload(task)))
    return path


def write_queue(path, messages, *, legacy_schema=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        extra = "" if legacy_schema else ", status TEXT, processing_started_at INTEGER"
        connection.execute(
            "CREATE TABLE queue_messages (id INTEGER PRIMARY KEY, queue_name TEXT, "
            f"message_id TEXT, data TEXT, timestamp INTEGER{extra})"
        )
        for index, (payload, status, timestamp) in enumerate(messages):
            envelope = {"id": f"message-{index}", "data": payload, "timestamp": timestamp}
            values = [index, "Semantic", envelope["id"], json.dumps(envelope), 100]
            if not legacy_schema:
                values.extend([status, 110 if status == "processing" else None])
            placeholders = ", ".join("?" for _ in values)
            connection.execute(f"INSERT INTO queue_messages VALUES ({placeholders})", values)


@pytest.mark.parametrize("layout", ["viking", "local", "direct"])
async def test_migration_preserves_records_and_is_repeatable(tmp_path, layout):
    root = tmp_path if layout == "direct" else tmp_path / layout
    history = append_task_event(
        None, kind="completed", status="completed", stage="done", recorded_at="2026-01-01T00:00:00Z"
    )
    history["dropped_count"] = 3
    task = TaskRecord(
        task_id="old",
        task_type="compile",
        status=TaskStatus.COMPLETED,
        created_at=0,
        updated_at=12.5,
        resource_id="viking://user/alice/a",
        meta={"submission_token": "keep-me", "internal": True},
        auth={"delegated": {"roles": ["USER"]}},
        result={"output": "kept"},
        error="old error",
        stage="done",
        processing_seconds=4.5,
        execution_events=history,
        **OWNER,
    )
    task_file = write_task(root, task)
    system = TaskRecord(
        task_id="system-task",
        task_type="fs_rm",
        status=TaskStatus.COMPLETED,
        account_id="_system",
        user_id="root",
    )
    write_task(root, system)
    (task_file.parent / ".redirect.json").write_text("storage metadata")
    (task_file.parent / "directory.json").mkdir()
    source = task_file.read_bytes()
    destination = tmp_path / "task_store/tasks.db"
    arguments = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": destination,
        "skip_queue_works": True,
    }
    dry = await migrate(**arguments)
    assert dry.task_files_seen == 2 and dry.failed == 0
    assert not destination.parent.exists()
    result = await migrate(**arguments, execute=True)
    assert result.tasks_inserted == 2 and result.failed == 0
    repeated = await migrate(**arguments, execute=True)
    assert repeated.tasks_inserted == 0 and repeated.tasks_updated == 2
    assert repeated.failed == 0
    assert task_file.read_bytes() == source
    store = SQLiteTaskWorkStore(params={"path": str(destination)})
    await store.load()
    try:
        restored = await store.get_task_with_events(task.task_id, **OWNER)
        assert _task_to_payload(restored) == _task_to_payload(task)
        assert await store.get_task(system.task_id, account_id="_system", user_id="root")
        assert await store.get_task(task.task_id, account_id="other", user_id="alice") is None
    finally:
        await store.close()


@pytest.mark.parametrize("legacy_schema", [False, True])
async def test_queue_work_survives_sqlite_restart_and_can_complete(tmp_path, legacy_schema):
    task = TaskRecord(task_id="queued", task_type="compile", **OWNER)
    write_task(tmp_path / "viking", task)
    payload = {"task_id": task.task_id, "_task_work_id": "work", **OWNER}
    queue_db = tmp_path / "_system/queue/queue.db"
    write_queue(
        queue_db,
        [
            (list(json.dumps(payload).encode()), "processing", "2026-01-01T00:00:00.123456789Z"),
            (json.dumps({"task_id": task.task_id, **OWNER}), "pending", None),
            ({"ordinary": "message"}, "pending", 100),
            ("untracked plaintext", "pending", 100),
            (json.dumps("untracked JSON string"), "pending", 100),
        ],
        legacy_schema=legacy_schema,
    )
    before = queue_db.read_bytes()
    destination = tmp_path / "task_store/tasks.db"
    arguments = {"workspace": tmp_path, "config": {}, "sqlite_path": destination, "execute": True}
    result = await migrate(**arguments)
    assert result.failed == 0 and result.works_inserted == 2
    repeated = await migrate(**arguments)
    assert repeated.failed == 0 and repeated.works_updated == 2
    if legacy_schema:
        assert queue_db.read_bytes() == before
    else:
        with sqlite3.connect(queue_db) as connection:
            assert connection.execute(
                "SELECT status, processing_started_at FROM queue_messages WHERE id=0"
            ).fetchone() == ("pending", None)
    store = SQLiteTaskWorkStore(params={"path": str(destination)})
    await store.load()
    try:
        work = await store.get_work(task.task_id, "work", **OWNER)
        assert work.state == WorkState.PENDING and work.started_at is None
        assert work.enqueued_at == pytest.approx(1767225600.123456)
        legacy = await store.get_work(task.task_id, "queuefs:message-1", **OWNER)
        assert legacy.enqueued_at == 100
        assert (await store.start_work(task.task_id, "work", **OWNER)).is_applied
        assert (
            await store.finish_work(
                task.task_id,
                "work",
                WorkState.DONE,
                error=None,
                errors=None,
                context_count_delta=1,
                **OWNER,
            )
        ).is_applied
        assert (await store.get_work(task.task_id, "work", **OWNER)).state == WorkState.DONE
    finally:
        await store.close()


async def test_partial_failure_and_retry(tmp_path, capsys):
    root = tmp_path / "viking"
    valid = TaskRecord(task_id="valid", task_type="compile", **OWNER)
    invalid = TaskRecord(task_id="invalid", task_type="compile", **OWNER)
    write_task(root, valid)
    invalid_file = write_task(root, invalid)
    invalid_file.write_text("broken JSON")
    queue_db = root / "_system/queue/queue.db"
    write_queue(
        queue_db,
        [
            ({"task_id": invalid.task_id, **OWNER}, "pending", 100),
            ({"task_id": valid.task_id, "account_id": "other", "user_id": "alice"}, "pending", 100),
            ({"task_id": valid.task_id}, "pending", 100),
        ],
    )
    arguments = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "execute": True,
    }
    result = await migrate(**arguments)
    assert result.tasks_inserted == 1 and result.failed == 4 and result.works_inserted == 0
    assert "queue work is missing identity fields" in capsys.readouterr().err
    write_task(root, invalid)
    repaired = await migrate(**arguments)
    assert repaired.tasks_inserted == 1 and repaired.tasks_updated == 1
    assert repaired.works_inserted == 1 and repaired.failed == 2


@pytest.mark.parametrize("on_conflict", ["update", "skip", "fail"])
async def test_conflicting_target_uses_requested_policy(tmp_path, on_conflict):
    task = TaskRecord(task_id="existing", task_type="compile", **OWNER)
    source = write_task(tmp_path / "viking", task)
    args = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "execute": True,
        "skip_queue_works": True,
    }
    assert (await migrate(**args)).failed == 0
    task.meta["changed"] = True
    write_task(tmp_path / "viking", task)
    result = await migrate(**args, on_conflict=on_conflict)
    assert result.failed == (1 if on_conflict == "fail" else 0)
    assert result.tasks_inserted == 0
    assert result.tasks_updated == (1 if on_conflict == "update" else 0)
    assert result.tasks_existing == (1 if on_conflict == "skip" else 0)
    assert json.loads(source.read_text())["meta"] == {"changed": True}
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        assert (await store.get_task(task.task_id, **OWNER)).meta == (
            {"changed": True} if on_conflict == "update" else {}
        )
    finally:
        await store.close()


async def test_encrypted_task_uses_original_key(tmp_path):
    config = {
        "encryption": {"enabled": True, "provider": "local", "local": {"root_key": "11" * 32}}
    }
    task = TaskRecord(task_id="encrypted", task_type="compile", **OWNER)
    path = write_task(tmp_path / "viking", task)
    encryptor = await bootstrap_encryption(config)
    ciphertext = await encryptor.encrypt(OWNER["account_id"], path.read_bytes())
    path.write_bytes(ciphertext)
    args = {"workspace": tmp_path, "sqlite_path": tmp_path / "tasks.db", "skip_queue_works": True}
    assert (await migrate(**args, config=config, execute=True)).failed == 0
    assert path.read_bytes() == ciphertext
    missing = {
        "encryption": {
            "enabled": True,
            "provider": "local",
            "local": {"key_file": str(tmp_path / "missing.key")},
        }
    }
    assert (await migrate(**args, config=missing)).failed == 1
    assert not (tmp_path / "missing.key").exists()


@pytest.mark.parametrize("change", ["owner", "unknown", "event", "missing"])
async def test_legacy_record_uses_reference_defaults(tmp_path, change):
    task = TaskRecord(task_id="invalid", task_type="compile", **OWNER)
    path = write_task(tmp_path / "viking", task)
    payload = json.loads(path.read_text())
    if change == "owner":
        payload["account_id"] = "other"
    elif change == "unknown":
        payload["new_field"] = "must not silently disappear"
    elif change == "event":
        payload["execution_events"] = {"items": [{"seq": 0}]}
    else:
        for key in ("task_id", "task_type", "status", "created_at", "updated_at"):
            del payload[key]
    path.write_text(json.dumps(payload))
    result = await migrate(
        workspace=tmp_path, config={}, sqlite_path=tmp_path / "tasks.db", skip_queue_works=True
    )
    assert result.failed == 0
    assert not (tmp_path / "tasks.db").exists()


async def test_reject_nonlocal_source_and_queue_destination(tmp_path):
    write_task(tmp_path / "viking", TaskRecord(task_id="a", task_type="compile", **OWNER))
    with pytest.raises(ValueError, match="local FileStore"):
        await migrate(
            workspace=tmp_path,
            config={"storage": {"agfs": {"backend": "s3"}}},
            sqlite_path=tmp_path / "tasks.db",
        )
    queue_db = tmp_path / "_system/queue/queue.db"
    write_queue(queue_db, [])
    with pytest.raises(ValueError, match="Destination"):
        await migrate(workspace=tmp_path, config={}, sqlite_path=queue_db)


def test_cli_refuses_locked_workspace_and_returns_nonzero(tmp_path):
    write_task(tmp_path / "viking", TaskRecord(task_id="a", task_type="compile", **OWNER))
    command = [
        sys.executable,
        "-m",
        "scripts.maintenance.task_store.migrate_file_to_sqlite",
        "--workspace",
        str(tmp_path),
        "--execute",
        "--skip-queue-works",
    ]
    lock = acquire_data_dir_lock(str(tmp_path))
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 1
        assert "migration_failed" in result.stderr
        assert not (tmp_path / "task_store/tasks.db").exists()
    finally:
        release_data_dir_lock(lock)
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    marker = "OPENVIKING_MIGRATION_RESULT "
    assert json.loads(result.stdout.splitlines()[-1].removeprefix(marker)) == {
        "failures": {"accounts": [], "tasks": []}
    }
    logs = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    assert (
        next(log for log in logs if log["event"] == "migration_stage_finished")["counts"][
            "tasks_inserted"
        ]
        == 1
    )


@pytest.mark.parametrize("policy", ["update", "skip", "fail"])
async def test_event_replacement_follows_task_conflict_policy(tmp_path, policy):
    history = append_task_event(
        None, kind="created", status="pending", stage=None, recorded_at="old"
    )
    task = TaskRecord(task_id="events", task_type="compile", execution_events=history, **OWNER)
    source = write_task(tmp_path / "viking", task)
    args = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "skip_queue_works": True,
        "execute": True,
    }
    assert (await migrate(**args)).task_events_inserted == 1
    payload = json.loads(source.read_text())
    payload["stage"] = "updated"
    payload["execution_events"] = {"items": []}
    source.write_text(json.dumps(payload))
    result = await migrate(**args, on_conflict=policy)
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        restored = await store.get_task_with_events(task.task_id, **OWNER)
        assert restored.stage == ("updated" if policy == "update" else None)
        assert restored.execution_events == (None if policy == "update" else history)
        assert result.failed == int(policy == "fail")
    finally:
        await store.close()


async def test_bad_event_rolls_back_task_and_event_replacement(tmp_path):
    history = append_task_event(
        None, kind="created", status="pending", stage=None, recorded_at="old"
    )
    task = TaskRecord(task_id="atomic", task_type="compile", execution_events=history, **OWNER)
    source = write_task(tmp_path / "viking", task)
    args = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "skip_queue_works": True,
        "execute": True,
    }
    await migrate(**args)
    payload = json.loads(source.read_text())
    payload["stage"] = "must roll back"
    event = history["items"][0]
    payload["execution_events"]["items"] = [event, event]
    source.write_text(json.dumps(payload))
    result = await migrate(**args)
    assert result.failed == 1 and result.tasks_updated == 0
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        restored = await store.get_task_with_events(task.task_id, **OWNER)
        assert restored.stage is None and restored.execution_events == history
    finally:
        await store.close()


async def test_target_owner_cannot_be_reassigned(tmp_path):
    task = TaskRecord(task_id="shared-id", task_type="compile", **OWNER)
    source = write_task(tmp_path / "viking", task)
    args = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "skip_queue_works": True,
        "execute": True,
    }
    await migrate(**args)
    source.unlink()
    task.account_id = "other"
    write_task(tmp_path / "viking", task)
    assert (await migrate(**args)).failed == 1
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        assert await store.get_task(task.task_id, **OWNER)
        assert await store.get_task(task.task_id, account_id="other", user_id="alice") is None
    finally:
        await store.close()


@pytest.mark.parametrize("policy", ["update", "skip", "fail"])
async def test_work_policy_and_failed_task_preserve_queue_leases(tmp_path, policy):
    good = TaskRecord(task_id="good", task_type="compile", **OWNER)
    bad = TaskRecord(task_id="bad", task_type="compile", **OWNER)
    write_task(tmp_path / "viking", good)
    bad_source = write_task(tmp_path / "viking", bad)
    queue_db = tmp_path / "_system/queue/queue.db"
    write_queue(queue_db, [({"task_id": "good", "work_id": "w", **OWNER}, "pending", 1)])
    args = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "execute": True,
    }
    await migrate(**args)
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        await store.start_work("good", "w", **OWNER)
        await store.finish_work(
            "good",
            "w",
            WorkState.DONE,
            error="preserve",
            errors=[],
            context_count_delta=3,
            **OWNER,
        )
    finally:
        await store.close()
    bad_source.write_text("broken")
    with sqlite3.connect(queue_db) as connection:
        connection.execute(
            "UPDATE queue_messages SET status='processing', processing_started_at=99"
        )
        for index, task_id in enumerate(["bad", "missing"], 1):
            envelope = {"id": f"message-{index}", "data": {"task_id": task_id, **OWNER}}
            connection.execute(
                "INSERT INTO queue_messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (index, "Semantic", envelope["id"], json.dumps(envelope), 1, "processing", 99),
            )
    # Filter source Tasks so fail mode exercises Work conflict, not Task conflict.
    result = await migrate(**args, on_conflict=policy, account_id="no-source")
    assert result.failed == (2 if policy == "fail" else 1)
    # bad already exists in the target and was intentionally not selected.
    assert result.works_reconstructed == (2 if policy != "fail" else 1)
    with sqlite3.connect(queue_db) as connection:
        rows = connection.execute(
            "SELECT status, processing_started_at FROM queue_messages ORDER BY id"
        ).fetchall()
    assert rows[0] == (("processing", 99) if policy == "fail" else ("pending", None))
    assert rows[1] == ("pending", None)
    assert rows[2] == ("processing", 99)
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        work = await store.get_work("good", "w", **OWNER)
        assert work.state == (WorkState.PENDING if policy == "update" else WorkState.DONE)
        assert work.error == "preserve" and work.context_count == 3
        assert work.finished_at is not None
    finally:
        await store.close()
    # Selecting the corrupt source now blocks its Work even though the target Task exists.
    with sqlite3.connect(queue_db) as connection:
        connection.execute(
            "UPDATE queue_messages SET status='processing', processing_started_at=99 WHERE id=1"
        )
    result = await migrate(**args)
    assert result.invalid_task_files == 1
    with sqlite3.connect(queue_db) as connection:
        assert connection.execute("SELECT status FROM queue_messages WHERE id=1").fetchone() == (
            "processing",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"_task_id": "legacy", "work": {"id": "w"}, "auth": OWNER},
        {"payload": {"task": {"id": "legacy"}, "task_work_id": "w", "context": OWNER}},
        {
            "data": {
                "task": {"task_id": "legacy"},
                "work": {"work_id": "w"},
                "task_account_id": "acme",
                "task_user_id": "alice",
            }
        },
        {
            "task_id": "legacy",
            "work_id": "w",
            "account_id": "wrong",
            "user_id": "wrong",
            "payload": {"_task_account_id": "acme", "_task_user_id": "alice"},
        },
    ],
)
async def test_legacy_queue_identity_aliases(tmp_path, payload):
    write_task(tmp_path / "viking", TaskRecord(task_id="legacy", task_type="compile", **OWNER))
    queue_db = tmp_path / "_system/queue/queue.db"
    write_queue(queue_db, [(payload, "processing", "not a timestamp")])
    result = await migrate(
        workspace=tmp_path, config={}, sqlite_path=tmp_path / "tasks.db", execute=True
    )
    assert result.failed == 0 and result.works_inserted == 1
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        work = await store.get_work("legacy", "w", **OWNER)
        assert work and work.enqueued_at == 100
    finally:
        await store.close()


async def test_terminal_task_work_is_reconstructed_and_queue_filter_applies(tmp_path):
    task = TaskRecord(task_id="done", task_type="compile", status=TaskStatus.COMPLETED, **OWNER)
    write_task(tmp_path / "viking", task)
    queue_db = tmp_path / "_system/queue/queue.db"
    write_queue(
        queue_db,
        [
            ({"task_id": "done", "work_id": "selected", **OWNER}, "processing", 1),
            ({"task_id": "done", "work_id": "excluded", **OWNER}, "processing", 1),
        ],
    )
    with sqlite3.connect(queue_db) as connection:
        connection.execute("UPDATE queue_messages SET queue_name='Other' WHERE id=1")
    result = await migrate(
        workspace=tmp_path,
        config={},
        sqlite_path=tmp_path / "tasks.db",
        execute=True,
        queue_names={"Semantic"},
    )
    assert result.failed == 0 and result.works_inserted == 1
    assert result.queue_messages_seen == 1 and result.works_reconstructed == 1
    with sqlite3.connect(queue_db) as connection:
        assert connection.execute("SELECT status FROM queue_messages ORDER BY id").fetchall() == [
            ("pending",),
            ("processing",),
        ]


async def test_missing_or_invalid_queue_fails_before_target_is_created(tmp_path):
    write_task(tmp_path / "viking", TaskRecord(task_id="a", task_type="compile", **OWNER))
    args = {
        "workspace": tmp_path,
        "config": {},
        "sqlite_path": tmp_path / "tasks.db",
        "execute": True,
    }
    with pytest.raises(ValueError, match="does not exist"):
        await migrate(**args)
    queue_db = tmp_path / "_system/queue/queue.db"
    queue_db.parent.mkdir(parents=True)
    with sqlite3.connect(queue_db) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        await migrate(**args)
    assert not (tmp_path / "tasks.db").exists()


def test_cli_redacts_bad_values_and_keeps_partial_summary(tmp_path):
    good = TaskRecord(task_id="a-good", task_type="compile", **OWNER)
    bad = TaskRecord(task_id="z-bad", task_type="compile", **OWNER)
    write_task(tmp_path / "viking", good)
    source = write_task(tmp_path / "viking", bad)
    payload = json.loads(source.read_text())
    payload["created_at"] = "SECRET_AUTH_VALUE"
    source.write_text(json.dumps(payload))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.maintenance.task_store.migrate_file_to_sqlite",
            "tasks",
            "--workspace",
            str(tmp_path),
            "--execute",
            "--skip-queue-works",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "SECRET_AUTH_VALUE" not in result.stdout + result.stderr
    public = json.loads(result.stdout.splitlines()[-1].split(" ", 1)[1])
    assert public["failures"]["tasks"][0]["task_id"] == "z-bad"
    logs = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    counts = next(log["counts"] for log in logs if log.get("stage") == "tasks")
    assert counts["tasks_inserted"] == 1 and counts["failed"] == 1


async def test_config_env_account_user_table_and_service_filters(tmp_path, monkeypatch):
    from scripts.maintenance.task_store.migrate_file_to_sqlite import (
        load_config,
        workspace_from_config,
    )

    monkeypatch.setenv("MIGRATION_WORKSPACE", str(tmp_path))
    config_file = tmp_path / "ov.conf"
    config_file.write_text(
        json.dumps(
            {
                "storage": {
                    "agfs": {"path": "$MIGRATION_WORKSPACE"},
                    "task_store": {
                        "params": {"service_name": "configured-service", "table": "custom_tasks"}
                    },
                }
            }
        )
    )
    config = load_config(config_file)
    assert workspace_from_config(config) == tmp_path
    write_task(tmp_path / "viking", TaskRecord(task_id="included", task_type="compile", **OWNER))
    write_task(
        tmp_path / "viking",
        TaskRecord(
            task_id="excluded",
            task_type="compile",
            account_id="other",
            user_id="alice",
        ),
    )
    result = await migrate(
        workspace=tmp_path,
        config=config,
        sqlite_path=tmp_path / "tasks.db",
        execute=True,
        account_id="acme",
        user_id="alice",
        skip_queue_works=True,
    )
    assert result.task_files_seen == 1 and result.tasks_inserted == 1
    store = SQLiteTaskWorkStore(
        params={
            "path": str(tmp_path / "tasks.db"),
            "table": "custom_tasks",
            "service_name": "configured-service",
        }
    )
    await store.load()
    try:
        assert await store.get_task("included", **OWNER)
        assert await store.get_task("excluded", account_id="other", user_id="alice") is None
    finally:
        await store.close()


async def test_fatal_database_error_stops_phase_and_keeps_partial_summary(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from scripts.maintenance.task_store import migrate_file_to_sqlite as migration

    for task_id in ("a-first", "b-fatal", "c-unattempted"):
        write_task(tmp_path / "viking", TaskRecord(task_id=task_id, task_type="compile", **OWNER))
    original = migration._insert_task

    async def insert(store, payload, policy):
        if payload["task_id"] == "b-fatal":
            raise OperationalError(
                "SECRET_SQL", {"auth": "SECRET_PARAM"}, Exception("SECRET_ERROR")
            )
        return await original(store, payload, policy)

    monkeypatch.setattr(migration, "_insert_task", insert)
    summary = migration.TaskSummary()
    with pytest.raises(migration.MigrationAborted):
        await migration.migrate(
            workspace=tmp_path,
            config={},
            sqlite_path=tmp_path / "tasks.db",
            execute=True,
            skip_queue_works=True,
            summary=summary,
        )
    assert summary.tasks_inserted == 1 and summary.failed == 1 and summary.task_files_seen == 2
    assert "SECRET" not in json.dumps(summary.failures)
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    await store.load()
    try:
        assert await store.get_task("a-first", **OWNER)
        assert await store.get_task("c-unattempted", **OWNER) is None
    finally:
        await store.close()


def test_queue_lease_reset_handles_more_than_sqlite_variable_limit(tmp_path):
    from scripts.maintenance.task_store.migrate_file_to_sqlite import (
        reset_processing_queue_messages,
    )

    queue_db = tmp_path / "queue.db"
    write_queue(queue_db, [({}, "processing", 1)] * 1100)
    selected = {f"message-{i}" for i in range(1050)}
    assert reset_processing_queue_messages(queue_db, selected) == 1050
    with sqlite3.connect(queue_db) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM queue_messages WHERE status='processing'"
            ).fetchone()[0]
            == 50
        )


def test_cli_database_setup_error_is_redacted(tmp_path):
    write_task(tmp_path / "viking", TaskRecord(task_id="a", task_type="compile", **OWNER))
    target = tmp_path / "tasks.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE ov_tasks (secret_column TEXT)")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.maintenance.task_store.migrate_file_to_sqlite",
            "--workspace",
            str(tmp_path),
            "--sqlite-path",
            str(target),
            "--execute",
            "--skip-queue-works",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr and "[SQL:" not in result.stderr
    assert "secret_column" not in result.stdout + result.stderr
    public = json.loads(result.stdout.splitlines()[-1].split(" ", 1)[1])
    assert len(public["failures"]["tasks"]) == 1
