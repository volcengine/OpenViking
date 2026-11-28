"""Configuration, run, and recovery contracts for registered and transient Compile."""

import asyncio
import json
from copy import deepcopy
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from openviking.service.compile_service import CompileService
from openviking.service.compile_store_sqlite import SQLiteCompileStore
from openviking.service.compile_targets import (
    CompileLifecycle,
    CompileStateStore,
    execution_signature,
    new_record,
    new_run,
)
from openviking.service.external_task_service import (
    SUBMIT_EXHAUSTED,
    ExternalTaskError,
    ExternalTaskResultSaveError,
    ExternalTaskService,
    ExternalTaskSnapshot,
)
from openviking.service.task_domain import (
    TaskPatch,
    TaskRecord,
    TaskStatus,
    WorkState,
)
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
from openviking.service.task_tracker import TaskTracker
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.utils.compile_recovery import source_set_digest
from openviking_cli.exceptions import (
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.utils.config.open_viking_config import CompileApiConfig
from tests.test_task_tracker import _FakeAgfs

SOURCE = "viking://resources/source"
TARGET = "viking://resources/output"
SKILL = "viking://system/compile/example-skills/llm-wiki"
PARAMS = {"from": [SOURCE], "to": TARGET, "skill": SKILL, "instruction": None, "args": {}}


class AsyncFiles:
    """Shared JSON storage and distributed-lock stand-in for independent lifecycle instances."""

    def __init__(self):
        self.files = {}
        self.locks = {}
        self.leases = {}

    async def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def stat(self, path, **kwargs):
        if path not in self.files:
            raise FileNotFoundError(path)
        return {"isDir": False}

    async def write(self, path, body, **kwargs):
        self.files[path] = body

    async def ensure_parent_dirs(self, path, **kwargs):
        pass

    async def ls(self, path):
        prefix = path.rstrip("/") + "/"
        names = {}
        for filename in self.files:
            if filename.startswith(prefix):
                rest = filename[len(prefix) :]
                name = rest.split("/", 1)[0]
                names[name] = {"name": name, "isDir": "/" in rest}
        return list(names.values())

    async def rm(self, path, **kwargs):
        self.files.pop(path, None)

    async def pathlock_acquire_exact(self, path, **kwargs):
        lock = self.locks.setdefault(path, asyncio.Lock())
        await lock.acquire()
        token = uuid4().hex
        self.leases[token] = lock
        return {"lease_ref": token}

    pathlock_acquire_tree = pathlock_acquire_exact

    async def pathlock_release(self, lease):
        self.leases.pop(lease["lease_ref"]).release()

    async def run(self, *args, **kwargs):
        return []


class Tracker:
    """Keep durable copies independent of the task objects held by workers."""

    def __init__(self):
        self.tasks = {}
        self._store = self
        self.cancelled = set()

    async def list_durable(self, account_id, **kwargs):
        return [deepcopy(t) for t in self.tasks.values() if t.account_id == account_id]

    async def get_durable_task(self, task_id, *, account_id, user_id):
        if self._store is not self:
            return await self._store.get_durable_task(
                task_id, account_id=account_id, user_id=user_id
            )
        task = self.tasks.get(task_id)
        if task is not None and (task.account_id, task.user_id) == (account_id, user_id):
            return deepcopy(task)
        return None

    async def patch_task(self, task_id, patch, **kwargs):
        task = self.tasks[task_id]
        for key, value in (patch.auth or {}).items():
            task.auth[key] = deepcopy(value)
        return SimpleNamespace(accepted=True)

    async def get_task_auth(self, task_id, **kwargs):
        return deepcopy(self.tasks[task_id].auth)

    async def is_cancellation_requested(self, task_id, **kwargs):
        return task_id in self.cancelled


class CaseInsensitiveFiles(AsyncFiles):
    """Model a filesystem where differently cased paths address the same files and locks."""

    async def read(self, path):
        return await super().read(path.lower())

    async def stat(self, path, **kwargs):
        return await super().stat(path.lower(), **kwargs)

    async def write(self, path, body, **kwargs):
        return await super().write(path.lower(), body, **kwargs)

    async def ls(self, path):
        return await super().ls(path.lower())

    async def rm(self, path, **kwargs):
        return await super().rm(path.lower(), **kwargs)

    async def pathlock_acquire_exact(self, path, **kwargs):
        return await super().pathlock_acquire_exact(path.lower(), **kwargs)


class SnapshotFS:
    """Record committed scopes and return deterministic snapshot IDs for lifecycle checks."""

    def __init__(self, files):
        self._async_agfs = files
        self.commits = []
        self.restore = AsyncMock(return_value={"result": "applied", "new_commit_oid": "f" * 40})
        self.source_changes = []
        self.output_changes = []
        self._can_access_many = AsyncMock(return_value={})
        self._schedule_restore_reindex_for_paths = AsyncMock(return_value=None)
        self._schedule_vector_rebuild = Mock()

    def _uri_to_path(self, uri, **kwargs):
        return "/local/acme/" + uri.removeprefix("viking://")

    _uri_to_tree_path = _uri_to_path

    async def commit(self, *, paths, **kwargs):
        self.commits.append(list(paths))
        return {"commit_oid": f"{len(self.commits):040x}"}

    async def diff_paths(self, *, dirs, **kwargs):
        return deepcopy(self.output_changes if dirs == [TARGET] else self.source_changes)


def make_store(files):
    store = CompileStateStore.__new__(CompileStateStore)
    store._agfs = files
    return store


@pytest.fixture
def setup(monkeypatch):
    files = AsyncFiles()
    tracker = Tracker()
    fs = SnapshotFS(files)
    store = make_store(files)
    finalize = AsyncMock(side_effect=AssertionError("Compile must not wait for target tagging"))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.server.dependencies.get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(finalize_compile_directories=finalize),
        ),
    )
    return SimpleNamespace(
        files=files,
        tracker=tracker,
        fs=fs,
        store=store,
        lifecycle=CompileLifecycle(store, fs),
        finalize=finalize,
    )


@pytest.fixture
def checkpoint_outage(setup, monkeypatch):
    """Reject one matching execution checkpoint, then allow recovery writes."""

    def arm(matches):
        patch_task = setup.tracker.patch_task

        async def fail_once(task_id, patch, **kwargs):
            checkpoint = (patch.auth or {}).get("compile_execution") or {}
            if matches(checkpoint):
                monkeypatch.setattr(setup.tracker, "patch_task", patch_task)
                raise OSError("checkpoint unavailable")
            return await patch_task(task_id, patch, **kwargs)

        monkeypatch.setattr(setup.tracker, "patch_task", fail_once)

    return arm


@pytest.fixture
def state_outage(setup, monkeypatch):
    """Reject one matching configuration write, then allow recovery writes."""

    def arm(matches):
        write = setup.files.write

        async def fail_once(path, body, **kwargs):
            if path.endswith("/state.json") and matches(json.loads(body)):
                monkeypatch.setattr(setup.files, "write", write)
                raise OSError("state unavailable")
            return await write(path, body, **kwargs)

        monkeypatch.setattr(setup.files, "write", fail_once)

    return arm


def make_task(
    setup,
    *,
    compile_name=None,
    record=None,
    created_at=100.0,
    trigger="manual",
    task_id=None,
    owner_user_id="alice",
    user_id="alice",
):
    request = deepcopy(PARAMS)
    request["compile"] = {"trigger": trigger, "auto_update": False, "update_schedule": None}
    if compile_name:
        request.update({key: deepcopy(record[key]) for key in PARAMS})
        request["compile"].update(
            compile_name=compile_name,
            owner_user_id=owner_user_id,
            input_signature=execution_signature(record),
            auto_update=record["auto_update"],
            update_schedule=record["update_schedule"],
        )
    task = TaskRecord(
        task_id=task_id or "cmp_" + uuid4().hex,
        task_type="compile",
        account_id="acme",
        user_id=user_id,
        created_at=created_at,
        meta={"request": request},
    )
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    return task


@pytest.fixture
async def registered(setup):
    """Create a saved configuration and its unstarted task for lifecycle scenarios."""
    compile_name = await setup.store.create(
        "acme", "alice", new_record(PARAMS), compile_name="wiki"
    )
    record = await setup.store.get("acme", "alice", compile_name)
    return record, compile_name, make_task(setup, compile_name=compile_name, record=record)


@pytest.fixture
def legacy_execution(setup, registered):
    """Build the persisted Run/private checkpoint shapes written by v0.5.6."""

    def build(configured, phase):
        record, name, named_task = registered
        task = named_task if configured else make_task(setup)
        task.meta.pop("compile", None)
        task.resource_id = SOURCE
        task.status = TaskStatus.PENDING if phase == "queued" else TaskStatus.RUNNING
        run = {
            **new_run(task),
            "status": "queued" if phase == "queued" else "running",
            "started_at": None if phase == "queued" else 101.0,
        }
        run["meta"]["trigger"] = "initial" if configured else "manual"
        run["meta"]["mode"] = None if phase == "queued" else "full"
        checkpoint = {}
        if phase != "queued":
            run["meta"]["start_snapshot"] = "1" * 40
            setup.fs.commits.append([TARGET, SOURCE] if configured else [TARGET])
            checkpoint = {
                "phase": "running",
                "progress_before": None,
                "baseline": None,
                "plan": {
                    "base": None,
                    "head": "1" * 40 if configured else None,
                    "diff_dirs": [],
                    "full_dirs": [SOURCE],
                    "execution": {"mode": "full"},
                    "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
                },
                "noop": False,
            }
        if phase in {"committing", "terminal", "finished"}:
            checkpoint["phase"] = "committing"
            run["status"] = "committing"
        if phase in {"terminal", "finished"}:
            run.update(status="completed", finished_at=102.0, result={"created": 7})
            run["meta"]["finish_snapshot"] = "2" * 40
            setup.fs.commits.append([TARGET])
        if phase == "finished":
            checkpoint["phase"] = "finished"
            record = deepcopy(record)
            record["progress"] = {
                "source_commit": run["meta"]["start_snapshot"],
                "processed_roots": [SOURCE],
                "instruction": None,
                "model_name": "",
                "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
            }
        if phase == "rolling_back":
            checkpoint.update(phase="rolling_back", terminal_status="cancelled")
            run["meta"]["finish_snapshot"] = "f" * 40
        path = (
            setup.store._record_path("acme", "alice", name).rsplit("/", 1)[0]
            + f"/runs/{task.task_id}.json"
        )
        if configured:
            setup.files.files[setup.store._record_path("acme", "alice", name)] = json.dumps(
                record
            ).encode()
            setup.files.files[path] = json.dumps(run).encode()
        elif phase != "queued":
            checkpoint["snapshots"] = {
                key: run["meta"][key]
                for key in ("start_snapshot", "finish_snapshot", "skill_snapshot_id")
            }
            checkpoint["outcome"] = {
                key: run[key] for key in ("status", "started_at", "finished_at", "result", "error")
            }
        task.auth["compile_execution"] = checkpoint
        setup.tracker.tasks[task.task_id] = deepcopy(task)
        setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
        return task, run, path

    return build


@pytest.mark.parametrize(
    ("configured", "phase", "cancel"),
    [
        pytest.param(True, "queued", False, id="named_queued_missing_run"),
        pytest.param(True, "running", True, id="named_cancel"),
        pytest.param(True, "committing", False, id="named_committing"),
        pytest.param(True, "terminal", False, id="named_terminal"),
        pytest.param(True, "finished", False, id="named_finished"),
        pytest.param(True, "finished", True, id="named_finished_late_cancel"),
        pytest.param(True, "rolling_back", True, id="named_rollback"),
        pytest.param(False, "running", True, id="one_off_cancel"),
        pytest.param(False, "terminal", False, id="one_off_terminal"),
        pytest.param(False, "rolling_back", True, id="one_off_rollback"),
    ],
)
async def test_legacy_execution_recovery(setup, legacy_execution, configured, phase, cancel):
    """An upgrade retains the old execution's snapshots, plan and finalization contract."""
    task, old_run, path = legacy_execution(configured, phase)
    if phase == "queued":
        setup.files.files.pop(path)
    old_plan = deepcopy(task.auth["compile_execution"].get("plan"))
    if cancel:
        setup.tracker.cancelled.add(task.task_id)
        task.status = TaskStatus.CANCELLING
    expected_status = "cancelled" if cancel and phase != "finished" else "completed"
    lifecycle = CompileLifecycle(setup.store, setup.fs)
    start = await lifecycle.on_start(task)
    if phase in {"queued", "running"}:
        assert start == "proceed"
    else:
        assert start.status == expected_status
    if old_plan:
        assert task.auth["compile_execution"]["plan"] == old_plan
    terminal = (
        start
        if isinstance(start, ExternalTaskSnapshot)
        else ExternalTaskSnapshot(status="completed")
    )
    outcome = await lifecycle.on_finish(task, terminal)
    run = await lifecycle._read_run(task)
    assert outcome.status == run["status"] == expected_status
    assert run["meta"]["start_snapshot"] == (old_run["meta"]["start_snapshot"] or f"{1:040x}")
    assert run["meta"]["finish_snapshot"]
    assert task.resource_id == SOURCE
    assert "compile" not in task.meta
    if phase in {"terminal", "finished"}:
        assert outcome.result == old_run["result"]
        assert run["started_at"] == 101.0 and run["finished_at"] == 102.0
    elif not cancel:
        assert outcome.result == {"created": 1, "updated": 0, "deleted": 0}
    if configured:
        assert json.loads(setup.files.files[path]) == run
        progress = (await setup.store.get("acme", "alice", "wiki"))["progress"]
        assert progress.get("source_commit") == (
            None if expected_status == "cancelled" else run["meta"]["start_snapshot"]
        )
    else:
        assert (
            task.auth["compile_execution"]["snapshots"]["start_snapshot"]
            == (run["meta"]["start_snapshot"])
        )
    assert setup.fs.restore.await_count == (
        1 if expected_status == "cancelled" and phase != "rolling_back" else 0
    )
    if setup.fs.restore.await_count:
        assert (
            setup.fs.restore.await_args.kwargs["source_commit"] == old_run["meta"]["start_snapshot"]
        )
    commits = deepcopy(setup.fs.commits)
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    restarted = CompileLifecycle(setup.store, setup.fs)
    replay = await restarted.on_start(resumed)
    assert replay.status == expected_status
    assert (await restarted.on_finish(resumed, replay)).result == outcome.result
    assert setup.fs.commits == commits


@pytest.mark.parametrize(
    ("task_store", "configured", "phase"),
    [
        pytest.param("file", True, "running", id="named_existing_session"),
        pytest.param("sqlite", False, "cancel", id="one_off_cancellation"),
        pytest.param("sqlite", True, "finished", id="named_finished_settlement"),
    ],
)
async def test_legacy_task_work_settlement(
    setup, legacy_execution, monkeypatch, tmp_path, task_store, configured, phase
):
    """Recovered delivery settles its Task and Work without resubmitting existing sessions."""
    template, old_run, _ = legacy_execution(
        configured, "finished" if phase == "finished" else "running"
    )
    owner = {"account_id": template.account_id, "user_id": template.user_id}
    template.auth["external_task_id"] = "existing-session"
    if phase == "finished":
        template.auth["external_terminal_snapshot"] = {
            "status": "completed",
            "result": old_run["result"],
        }
    store = (
        SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
        if task_store == "sqlite"
        else PersistentTaskStore(_FakeAgfs())
    )
    tracker = TaskTracker(store)
    await tracker.load()
    for module in ("task_tracker", "external_task_service"):
        monkeypatch.setattr(f"openviking.service.{module}.get_task_tracker", lambda: tracker)
    external = ExternalTaskService()
    provider = CompileService(CompileApiConfig(), external, setup.fs)
    provider.configure_local_backend("http://localhost:1", "")
    provider.configure_lifecycle(CompileLifecycle(setup.store, setup.fs))
    provider.submit = AsyncMock(return_value="new-session")
    provider.get = AsyncMock(return_value=ExternalTaskSnapshot(status="completed"))
    provider.cancel = AsyncMock(return_value=ExternalTaskSnapshot(status="cancelled"))
    archive = AsyncMock()
    monkeypatch.setattr(provider, "_client", lambda: SimpleNamespace(archive=archive))
    external.register(provider)
    try:
        task = await tracker.create(
            "compile",
            task_id=template.task_id,
            resource_id=template.resource_id,
            meta=template.meta,
            auth=template.auth,
            **owner,
        )
        await tracker.register_work(task.task_id, "work", "ExternalTask", **owner)
        await tracker.start_work(task.task_id, "work", queue_name="ExternalTask", **owner)
        if phase == "cancel":
            await tracker.cancel(task.task_id, **owner)
        assert await external.execute(task.task_id, **owner)
        await tracker.mark_work_done(task.task_id, "work", **owner)
        durable = await tracker.get(task.task_id, **owner)
        assert durable.status is (
            TaskStatus.CANCELLED if phase == "cancel" else TaskStatus.COMPLETED
        )
        assert durable.resource_id == SOURCE
        provider.submit.assert_not_awaited()
        assert provider.get.await_count == (1 if phase == "running" else 0)
        assert setup.fs.restore.await_count == (1 if phase == "cancel" else 0)
        if phase == "finished":
            assert durable.result == old_run["result"]
            assert await provider._lifecycle._read_run(durable) == old_run
            assert len(setup.fs.commits) == 2
        archive.assert_awaited_once()
        assert await tracker.get_work_state(task.task_id, "work", **owner) is (
            WorkState.CANCELLED if phase == "cancel" else WorkState.DONE
        )
        assert await external.execute(task.task_id, **owner)
        provider.submit.assert_not_awaited()
    finally:
        await tracker.close()


@pytest.mark.parametrize(
    ("backend", "deletion", "status"),
    [
        ("sqlite", "before_finish", "completed"),
        ("sqlite", "during_finish", "completed"),
        ("file", "task_save_retry", "completed"),
        ("file", "before_finish", "failed"),
        ("file", "before_finish", "cancelled"),
        ("file", "rollback_exhausted", "cancelled"),
    ],
)
async def test_deleted_history_settles_delegated_task(
    setup, monkeypatch, tmp_path, backend, deletion, status
):
    """Deleted history preserves the Runtime outcome and closes Work."""
    compile_store = (
        SQLiteCompileStore({"path": str(tmp_path / "compile.db")}, "test")
        if backend == "sqlite"
        else setup.store
    )
    if backend == "sqlite":
        await compile_store.load()
        await compile_store.create_config("acme", "alice", "wiki", new_record(PARAMS))
    else:
        await compile_store.create("acme", "alice", new_record(PARAMS), compile_name="wiki")
    lifecycle = CompileLifecycle(compile_store, setup.fs)
    record = await lifecycle.get_config("acme", "alice", "wiki")
    template = make_task(setup, compile_name="wiki", record=record, user_id="bob")
    owner = {"account_id": "acme", "user_id": "bob"}
    tracker = TaskTracker(SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")}))
    await tracker.load()
    for module in ("task_tracker", "external_task_service"):
        monkeypatch.setattr(f"openviking.service.{module}.get_task_tracker", lambda: tracker)
    external = ExternalTaskService()
    provider = CompileService(CompileApiConfig(), external, setup.fs)
    provider.configure_lifecycle(lifecycle)
    provider.submit = AsyncMock()
    provider.get = AsyncMock()
    provider.cancel = AsyncMock()
    monkeypatch.setattr(provider, "_client", lambda: SimpleNamespace(archive=AsyncMock()))
    external.register(provider)

    async def delete_history():
        if backend == "sqlite":
            await compile_store.delete_config("acme", "alice")
        else:
            prefix = compile_store.root("acme", "alice") + "/"
            for path in list(setup.files.files):
                if path.startswith(prefix):
                    setup.files.files.pop(path)

    try:
        task = await tracker.create(
            "compile",
            task_id=template.task_id,
            meta=template.meta,
            auth={"external_task_id": "existing-session"},
            **owner,
        )
        await tracker.start(task.task_id, **owner)
        await tracker.register_work(task.task_id, "work", "ExternalTask", **owner)
        await tracker.start_work(task.task_id, "work", queue_name="ExternalTask", **owner)
        await lifecycle.on_start(task)
        commits = deepcopy(setup.fs.commits)
        terminal = ExternalTaskSnapshot(
            status=status,
            result={"created": 7},
            error_code="RUNTIME_FAILED" if status == "failed" else None,
            error_message="Runtime failed" if status == "failed" else None,
        )
        expected_result = terminal.result
        if deletion == "during_finish":
            update_run = compile_store.update_run

            async def delete_before_update(*args, **kwargs):
                monkeypatch.setattr(compile_store, "update_run", update_run)
                await delete_history()
                return await update_run(*args, **kwargs)

            monkeypatch.setattr(compile_store, "update_run", delete_before_update)
        elif deletion != "task_save_retry":
            await delete_history()
        if deletion == "rollback_exhausted":
            auth = await tracker.get_task_auth(task.task_id, **owner)
            await tracker.patch_task(
                task.task_id,
                TaskPatch(
                    meta={"rollback_lock_retries": 60},
                    auth={
                        "compile_execution": {
                            **auth["compile_execution"],
                            "phase": "rollback_exhausted",
                        }
                    },
                ),
                **owner,
            )
        if deletion == "task_save_retry":
            setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
            with monkeypatch.context() as patch:
                patch.setattr(tracker, "complete", AsyncMock(side_effect=OSError("Task outage")))
                patch.setattr("openviking.service.external_task_service.asyncio.sleep", AsyncMock())
                with pytest.raises(ExternalTaskResultSaveError):
                    await external._apply_snapshot(terminal, task_id=task.task_id, auth={}, **owner)
            assert await tracker.get_work_state(task.task_id, "work", **owner) is WorkState.RUNNING
            expected_result = (await lifecycle.get_run("acme", "alice", "wiki", task.task_id))[
                "result"
            ]
            assert expected_result == {"created": 1, "updated": 0, "deleted": 0}
            commits = deepcopy(setup.fs.commits)
            await delete_history()
            external = ExternalTaskService()
            external.register(provider)
            provider.configure_lifecycle(CompileLifecycle(compile_store, setup.fs))
            assert await external.execute(task.task_id, **owner)
        else:
            assert await external._apply_snapshot(terminal, task_id=task.task_id, auth={}, **owner)
        if status == "failed":
            await tracker.mark_work_failed(task.task_id, "work", terminal.error_message, **owner)
        else:
            await tracker.mark_work_done(task.task_id, "work", **owner)
        finished = await tracker.get(task.task_id, **owner)
        assert finished.status is TaskStatus(status)
        if status != "cancelled":
            assert finished.result == expected_result
        assert (
            await tracker.get_work_state(task.task_id, "work", **owner)
            is {
                "completed": WorkState.DONE,
                "failed": WorkState.FAILED,
                "cancelled": WorkState.CANCELLED,
            }[status]
        )
        assert await lifecycle.get_config("acme", "alice", "wiki") is None
        assert await lifecycle.get_run("acme", "alice", "wiki", task.task_id) is None
        assert setup.fs.commits == commits
        setup.fs.restore.assert_not_awaited()
        provider.submit.assert_not_awaited()
        provider.get.assert_not_awaited()
        provider.cancel.assert_not_awaited()
        assert await external.execute(task.task_id, **owner)
    finally:
        await tracker.close()
        if backend == "sqlite":
            await compile_store.close()


@pytest.mark.parametrize("failure", ["missing_run", "config_unavailable", "active_runtime"])
async def test_deleted_history_settlement_requires_confirmed_deletion(
    setup, registered, monkeypatch, failure
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    setup.files.files.pop(setup.store._run_path("acme", "alice", name, task.task_id))
    if failure != "missing_run":
        setup.files.files.pop(setup.store._record_path("acme", "alice", name))
    if failure == "config_unavailable":
        monkeypatch.setattr(
            setup.lifecycle, "get_config", AsyncMock(side_effect=OSError("Config unavailable"))
        )
    snapshot = ExternalTaskSnapshot(
        status="running" if failure == "active_runtime" else "completed"
    )
    expected = OSError if failure == "config_unavailable" else NotFoundError
    with pytest.raises(expected):
        await setup.lifecycle.on_finish(task, snapshot)
    setup.fs.restore.assert_not_awaited()


@pytest.fixture
def compile_metric_events(monkeypatch):
    """Capture committed output facts with reporting enabled and no external exporter."""
    events = []
    monkeypatch.setattr(
        "openviking.observability.reporting.runtime.is_reporting_enabled", lambda: True
    )
    monkeypatch.setattr(
        "openviking.observability.reporting.facts.try_publish_event",
        lambda name, payload: events.append((name, deepcopy(payload))),
    )
    return events


@pytest.mark.parametrize("configured", [False, True])
async def test_snapshot_denial_keeps_start_time(setup, registered, monkeypatch, configured):
    """Snapshot permission failures retain start and finish times in both execution modes."""
    task = registered[2] if configured else make_task(setup)
    monkeypatch.setattr(setup.fs, "commit", AsyncMock(side_effect=PermissionDeniedError("denied")))
    with pytest.raises(PermissionDeniedError):
        await setup.lifecycle.on_start(task)
    await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="failed"))
    run = await setup.lifecycle._read_run(task)
    assert run["status"] == "failed" and 0 < run["started_at"] <= run["finished_at"]


@pytest.mark.parametrize("configured", [False, True])
async def test_output_metrics_preserve_identity_and_commit_time_on_recovery(
    setup, registered, monkeypatch, compile_metric_events, configured
):
    """Both execution modes replay durable output facts after interrupted finalization."""
    task = registered[2] if configured else make_task(setup)
    await setup.lifecycle.on_start(task)
    uri = TARGET + "/page.md"
    setup.fs.output_changes = [{"uri": uri, "change_type": "added"}]
    save_run = setup.lifecycle._save_run

    async def fail_terminal_save(task, run):
        """Keep the publication checkpoint durable while simulating a failed terminal write."""
        if run["finished_at"] is not None:
            raise OSError("run unavailable")
        await save_run(task, run)

    monkeypatch.setattr(setup.lifecycle, "_save_run", fail_terminal_save)
    monkeypatch.setattr("openviking.service.compile_targets.time.time", lambda: 1000)
    with pytest.raises(OSError, match="run unavailable"):
        await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    ((name, event),) = compile_metric_events
    assert name == "file_mutation"
    assert event["operation"] == "compile_commit"
    assert event["uri"] == uri
    assert event["task_id"] == task.task_id
    assert event["account_id"] == task.account_id
    assert event["user_id"] == task.user_id
    assert event["updated_at"] == 1000000

    monkeypatch.setattr("openviking.service.compile_targets.time.time", lambda: 2000)
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    resumed.auth = {}
    lifecycle = CompileLifecycle(setup.store, setup.fs)
    outcome = await lifecycle.on_finish(resumed, ExternalTaskSnapshot(status="completed"))
    assert outcome.status == "completed"
    assert compile_metric_events == [(name, event), (name, event)]
    assert len(setup.fs.commits) == 2
    await lifecycle.on_finish(resumed, ExternalTaskSnapshot(status="completed"))
    assert len(compile_metric_events) == 2


@pytest.mark.parametrize(
    "name", ["", " wiki", "wiki ", "wiki\n", "../wiki", "a/b", "a\\b", "文。", "a" * 65, None, 1]
)
async def test_invalid_names_are_rejected_without_storage_side_effects(setup, name):
    with pytest.raises(InvalidArgumentError):
        await setup.store.create("acme", "alice", new_record(PARAMS), compile_name=name)
    assert not setup.files.files
    assert not setup.files.locks


@pytest.mark.parametrize("name", ["a", "A_B-9", "a" * 64])
async def test_valid_name_boundaries_are_preserved(setup, name):
    assert await setup.store.create("acme", "alice", new_record(PARAMS), compile_name=name) == name
    assert (
        f"/local/acme/_system/compile/tasks/alice/{name.encode('ascii').hex()}/state.json"
        in setup.files.files
    )


async def test_case_insensitive_storage_preserves_names_and_run_histories():
    files = CaseInsensitiveFiles()
    store = make_store(files)
    names = ["Research", "Wiki", "research", "研发-A", "研发-a", "𠀀" * 32]
    for index, name in enumerate(names):
        record = new_record({**PARAMS, "instruction": name})
        await store.create("acme", "alice", record, compile_name=name)
        run = {"task_id": "cmp_" + str(index) * 32, "created_at": index}
        await store.put_run("acme", "alice", name, run)
        assert await store.get_run("acme", "alice", name, run["task_id"]) == run
        assert await store.list_runs("acme", "alice", name) == [run]
    assert await store.get("acme", "alice", "RESEARCH") is None
    for name in names:
        assert (await store.get("acme", "alice", name))["instruction"] == name
    assert [name async for name, _ in store.iter_records("acme")] == names


@pytest.mark.parametrize(
    "segment",
    [
        "Wiki",
        "ff",
        "2e2e",
        "6A",
        "61 62",
        "name_77696b69",
        "u-00000000",
    ],
)
async def test_configuration_discovery_ignores_noncanonical_directories(setup, segment):
    for name in ("ab", "j"):
        await setup.store.create("acme", "alice", new_record(PARAMS), compile_name=name)
    setup.files.files[f"/local/acme/_system/compile/tasks/alice/{segment}/state.json"] = b"{}"
    assert [name async for name, _ in setup.store.iter_records("acme")] == ["ab", "j"]


async def publish_config(lifecycle, name, reschedule):
    service = CompileService(Mock(), Mock(), SimpleNamespace(ensure_compile_target=AsyncMock()))
    service.configure_lifecycle(lifecycle)
    service._scheduler = SimpleNamespace(reschedule=reschedule)
    service._settings = AsyncMock(return_value=new_record(PARAMS))
    return await service.create_compile_task(
        SimpleNamespace(compile_name=name, args={}),
        ctx=SimpleNamespace(account_id="acme", user=SimpleNamespace(user_id="alice")),
    )


async def test_creation_locks_state_through_schedule_registration(setup):
    registered = asyncio.Event()
    publish = asyncio.Event()

    async def register(*args, **kwargs):
        registered.set()
        await publish.wait()

    async def callback():
        async with setup.lifecycle.edit_config("acme", "alice", "wiki") as record:
            return record["to"]

    first = asyncio.create_task(publish_config(setup.lifecycle, "wiki", register))
    await registered.wait()
    delivery = asyncio.create_task(callback())
    duplicate_registration = AsyncMock()
    second = asyncio.create_task(
        publish_config(
            CompileLifecycle(make_store(setup.files), setup.fs), "wiki", duplicate_registration
        )
    )
    await asyncio.sleep(0)
    assert not second.done() and not delivery.done()
    publish.set()
    assert await first == {"compile_name": "wiki"}
    assert await delivery == TARGET
    with pytest.raises(ConflictError, match="already exists"):
        await second
    duplicate_registration.assert_not_awaited()


async def test_concurrent_creators_with_same_name_remain_discoverable(setup):
    await asyncio.gather(
        *(
            setup.store.create("acme", owner, new_record(PARAMS), compile_name="wiki")
            for owner in ("alice", "bob")
        )
    )
    assert [
        (name, record["execution_context"]["owner_user_id"])
        async for name, record in setup.store.iter_records("acme")
    ] == [("wiki", "alice"), ("wiki", "bob")]


async def test_acceptance_times_are_idempotent_and_never_move_backwards(setup, registered):
    record, compile_name, first = registered
    later = make_task(
        setup, compile_name=compile_name, record=record, created_at=300, trigger="scheduled"
    )
    delayed = make_task(setup, compile_name=compile_name, record=record, created_at=200)
    await setup.lifecycle.record_queued(first)
    await asyncio.gather(
        setup.lifecycle.record_queued(later), setup.lifecycle.record_queued(delayed)
    )
    await setup.lifecycle.record_queued(first)
    current = await setup.store.get("acme", "alice", compile_name)
    assert current["task_first_create_time"] == record["task_first_create_time"]
    assert current["last_run_time"] == 300
    runs = await setup.store.list_runs("acme", "alice", compile_name)
    assert [run["meta"]["trigger"] for run in runs] == ["scheduled", "manual", "initial"]
    older = await setup.store.list_runs("acme", "alice", compile_name, cursor=delayed.task_id)
    assert older == [runs[-1]]


@pytest.mark.parametrize(
    "source,skill",
    [(SOURCE, SKILL), ("viking://agent/skills", "viking://agent/skills/compiler")],
)
async def test_full_execution_never_touches_compile_storage_or_source_snapshots(
    setup, source, skill
):
    class ForbiddenStore:
        def __getattr__(self, name):
            raise AssertionError(f"Full compile accesses configuration storage: {name}")

    lifecycle = CompileLifecycle(ForbiddenStore(), setup.fs)
    task = make_task(setup)
    task.meta["request"].update({"from": [source], "skill": skill})
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    assert await lifecycle.on_start(task) == "proceed"
    assert setup.fs.commits == [[TARGET]]
    plan = await lifecycle.runtime_changes(task.meta["request"], "acme", task.task_id, "alice")
    assert plan["execution"] == {"mode": "full"}
    assert plan["head"] is plan["base"] is None
    assert plan["full_dirs"] == [source] and plan["diff_dirs"] == []
    assert plan["skill_snapshot"]["snapshot_id"] is None
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    outcome = await lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert outcome.result == {"created": 1, "updated": 0, "deleted": 0}
    assert setup.fs.commits == [[TARGET], [TARGET]]
    assert not setup.files.files
    setup.finalize.assert_not_awaited()


@pytest.mark.parametrize("source_changed", [False, True])
async def test_scheduled_run_without_output_changes_preserves_manual_baseline(
    setup, registered, source_changed
):
    record, compile_name, first = registered
    setup.files.write = AsyncMock(wraps=setup.files.write)
    await setup.lifecycle.record_queued(first)
    assert await setup.lifecycle.on_start(first) == "proceed"
    first_plan = await setup.lifecycle.runtime_changes(
        first.meta["request"], "acme", first.task_id, "alice"
    )
    assert first_plan["execution"]["mode"] == "full"
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    await setup.lifecycle.on_finish(first, ExternalTaskSnapshot(status="completed"))
    paths = [call.args[0] for call in setup.files.write.await_args_list]
    assert sum(path.endswith("/state.json") for path in paths) == 2
    state = await setup.store.get("acme", "alice", compile_name)
    assert state["progress"]["source_commit"] == first_plan["head"]
    setup.fs.output_changes = []
    if source_changed:
        setup.fs.source_changes = [{"uri": SOURCE + "/source.md", "change_type": "modified"}]
    second = make_task(
        setup, compile_name=compile_name, record=state, created_at=200, trigger="scheduled"
    )
    await setup.lifecycle.record_queued(second)
    outcome = await setup.lifecycle.on_start(second)
    if source_changed:
        assert outcome == "proceed"
        outcome = ExternalTaskSnapshot(status="completed")
    else:
        assert isinstance(outcome, ExternalTaskSnapshot) and outcome.status == "completed"
    await setup.lifecycle.on_finish(second, outcome)
    run = await setup.store.get_run("acme", "alice", compile_name, second.task_id)
    await CompileLifecycle(setup.store, setup.fs).on_finish(
        second, ExternalTaskSnapshot(status="completed")
    )
    assert run["meta"]["trigger"] == "scheduled"
    assert run["meta"]["base_snapshot"] == first_plan["head"]
    assert (run["meta"]["finish_snapshot"] == run["meta"]["start_snapshot"]) != source_changed
    assert run["result"] == {"created": 0, "updated": 0, "deleted": 0}
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == state["progress"]


async def test_partial_delivery_retains_baseline_after_restart(setup, registered, monkeypatch):
    _, name, first = registered

    async def finish_after_restart(lifecycle, task, result):
        with monkeypatch.context() as patch:
            patch.setattr(setup.fs, "commit", AsyncMock(side_effect=OSError("Interrupted")))
            with pytest.raises(OSError, match="Interrupted"):
                await lifecycle.on_finish(
                    task, ExternalTaskSnapshot(status="completed", result=result)
                )
        restored = deepcopy(setup.tracker.tasks[task.task_id])
        restarted = CompileLifecycle(setup.store, setup.fs)
        terminal = ExternalTaskSnapshot(status="completed")
        outcome = await restarted.on_finish(restored, terminal)
        assert await restarted.on_finish(restored, terminal) == outcome
        return restarted, outcome.result

    await setup.lifecycle.on_start(first)
    setup.fs.output_changes = [{"uri": TARGET + "/baseline.md", "change_type": "added"}]
    await setup.lifecycle.on_finish(first, ExternalTaskSnapshot(status="completed"))
    previous = await setup.store.get("acme", "alice", name)
    task = make_task(setup, compile_name=name, record=previous, created_at=200)
    setup.fs.source_changes = [{"uri": SOURCE + "/missed.md", "change_type": "modified"}]
    assert await setup.lifecycle.on_start(task) == "proceed"
    setup.fs.output_changes = [{"uri": TARGET + "/good.md", "change_type": "added"}]
    restarted, result = await finish_after_restart(
        setup.lifecycle, task, {"completion_status": "partial"}
    )
    assert result == {
        "created": 1,
        "updated": 0,
        "deleted": 0,
        "completion_status": "partial",
    }
    retained = await setup.store.get("acme", "alice", name)
    assert retained["progress"] == previous["progress"]
    setup.fs.restore.assert_not_awaited()
    retry = make_task(setup, compile_name=name, record=retained, created_at=300)
    assert await restarted.on_start(retry) == "proceed"
    plan = await restarted.runtime_changes(retry.meta["request"], "acme", retry.task_id, "alice")
    assert plan["base"] == previous["progress"]["source_commit"]
    assert plan["diff_dirs"] == [SOURCE]
    expected = source_set_digest([SOURCE + "/missed.md"])
    assert plan["recovery_sources"] == expected

    setup.fs.output_changes = []
    await restarted.on_finish(
        retry,
        ExternalTaskSnapshot(status="completed", result={"recovery_proof": source_set_digest([])}),
    )
    retained = await setup.store.get("acme", "alice", name)
    assert retained["progress"] == previous["progress"]

    verified = make_task(setup, compile_name=name, record=retained, created_at=400)
    assert await restarted.on_start(verified) == "proceed"
    plan = await restarted.runtime_changes(
        verified.meta["request"], "acme", verified.task_id, "alice"
    )
    restarted, result = await finish_after_restart(
        restarted, verified, {"recovery_proof": expected}
    )
    assert result == {"created": 0, "updated": 0, "deleted": 0}
    committed = await setup.store.get("acme", "alice", name)
    assert committed["progress"]["source_commit"] == plan["head"]
    assert "partial_compile" not in committed["execution_context"]

    setup.fs.source_changes = []
    unchanged = make_task(setup, compile_name=name, record=committed, created_at=500)
    assert await restarted.on_start(unchanged) == ExternalTaskSnapshot(
        status="completed", result={"created": 0, "updated": 0, "deleted": 0}
    )
    setup.fs.source_changes = [{"uri": SOURCE + "/new.md", "change_type": "added"}]
    changed = make_task(setup, compile_name=name, record=committed, created_at=600)
    assert await restarted.on_start(changed) == "proceed"
    next_plan = await restarted.runtime_changes(
        changed.meta["request"], "acme", changed.task_id, "alice"
    )
    assert next_plan["base"] == plan["head"]
    assert "recovery_sources" not in next_plan


async def test_changes_new_roots_and_updated_configuration_do_not_rewrite_frozen_inputs(setup):
    record = new_record(PARAMS)
    record["progress"] = {
        "source_commit": "a" * 40,
        "processed_roots": [SOURCE],
        "instruction": None,
        "model_name": "",
        "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
    }
    record["from"] = [SOURCE, "viking://resources/added"]
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    task = make_task(setup, compile_name=compile_name, record=record)
    setup.fs.source_changes = [{"uri": SOURCE + "/gone.md", "change_type": "deleted"}]
    assert await setup.lifecycle.on_start(task) == "proceed"
    plan = await setup.lifecycle.runtime_changes(
        task.meta["request"], "acme", task.task_id, "alice"
    )
    assert plan["diff_dirs"] == [SOURCE]
    assert plan["full_dirs"] == ["viking://resources/added"]
    async with setup.store.edit("acme", "alice", compile_name) as current:
        current["instruction"] = "new instruction"
    resumed = CompileLifecycle(setup.store, setup.fs)
    stale_task = deepcopy(setup.tracker.tasks[task.task_id])
    stale_task.auth = {}
    assert await resumed.on_start(stale_task) == "proceed"
    assert (
        await resumed.runtime_changes(task.meta["request"], "acme", task.task_id, "alice") == plan
    )
    assert len(setup.fs.commits) == 1
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    await resumed.on_finish(stale_task, ExternalTaskSnapshot(status="completed"))
    updated = await setup.store.get("acme", "alice", compile_name)
    assert updated["instruction"] == "new instruction"
    assert updated["progress"] == record["progress"]
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["params"]["instruction"] is None


@pytest.mark.parametrize("status", ["failed", "cancelled", "OperationConflict"])
async def test_unsuccessful_runs_preserve_baseline_and_redact_persisted_errors(
    setup, status, compile_metric_events
):
    record = new_record(PARAMS)
    record["progress"] = {
        "source_commit": "a" * 40,
        "processed_roots": [SOURCE],
        "instruction": None,
        "model_name": "",
        "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
    }
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    setup.fs.source_changes = [{"uri": SOURCE + "/a.md", "change_type": "modified"}]
    task = make_task(setup, compile_name=compile_name, record=record)
    assert await setup.lifecycle.on_start(task) == "proceed"
    snapshot = ExternalTaskSnapshot(
        status=status,
        error_message="Bad request sk-secret_value Authorization: Bearer bearer-secret",
    )
    if status == "OperationConflict":
        task.stage = "submitting"
        rejected = await CompileService.on_cancel(
            SimpleNamespace(),
            task,
            external_task_id=None,
            payload={},
            private_payload={},
            connection={},
            failure=ExternalTaskError(status, snapshot.error_message, transient=False),
        )
        snapshot, status = rejected.snapshot, "failed"
    task.auth = {}
    outcome = await setup.lifecycle.on_finish(task, snapshot)
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["status"] == status
    assert run["meta"]["trigger"] == "initial"
    assert run["meta"]["finish_snapshot"] is not None
    setup.fs.restore.assert_awaited_once()
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == record["progress"]
    assert "secret_value" not in run["error"] and "bearer-secret" not in run["error"]
    assert "[REDACTED]" in run["error"]
    assert outcome.error_message == run["error"]
    restarted = CompileLifecycle(setup.store, setup.fs)
    assert await restarted.on_finish(task, snapshot) == outcome
    assert await restarted._read_run(task) == run
    setup.fs.restore.assert_awaited_once()
    assert compile_metric_events == []


async def test_duplicate_start_reuses_exact_snapshots_and_plan(setup, registered):
    record, compile_name, task = registered
    duplicate = deepcopy(task)
    another_worker = CompileLifecycle(setup.store, setup.fs)
    results = await asyncio.gather(
        setup.lifecycle.on_start(task), another_worker.on_start(duplicate)
    )
    assert results == ["proceed", "proceed"]
    assert setup.fs.commits == [[TARGET, SOURCE]]


async def test_start_resumes_durable_checkpoint_despite_stale_cache(setup):
    files = _FakeAgfs()
    reader = setup.tracker._store = PersistentTaskStore(files)
    writer = PersistentTaskStore(files)
    task = make_task(setup)
    owner = {"account_id": task.account_id, "user_id": task.user_id}
    task.auth = {"compile_execution": {"phase": "running"}}
    await reader.create(task)
    latest = {"compile_execution": {"phase": "committing"}}
    await writer.patch_task(task.task_id, TaskPatch(auth=latest), **owner)
    assert (await reader.get_task(task.task_id, **owner)).auth == task.auth
    task.auth["external_task_id"] = "runtime-session"
    result = await setup.lifecycle.on_start(task)
    assert isinstance(result, ExternalTaskSnapshot) and result.status == "completed"
    assert task.auth == {**latest, "external_task_id": "runtime-session"}
    assert setup.fs.commits == []


async def test_committing_recovery_does_not_resubmit_or_replace_the_start_plan(setup, registered):
    record, compile_name, task = registered
    await setup.lifecycle.on_start(task)
    original_plan = deepcopy(setup.tracker.tasks[task.task_id].auth["compile_execution"]["plan"])
    commit = setup.fs.commit
    setup.fs.commit = AsyncMock(side_effect=ResourceBusyError("snapshot is busy"))
    outcome = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert outcome.status == "running" and outcome.stage == "committing"
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == {}
    restarted = CompileLifecycle(setup.store, setup.fs)
    resumed = deepcopy(task)
    resumed.auth = {}
    terminal = await restarted.on_start(resumed)
    assert isinstance(terminal, ExternalTaskSnapshot) and terminal.status == "completed"
    assert len(setup.fs.commits) == 1
    setup.fs.commit = commit
    assert (await restarted.on_finish(resumed, terminal)).status == "completed"
    assert setup.tracker.tasks[task.task_id].auth["compile_execution"]["plan"] == original_plan
    assert len(setup.fs.commits) == 2


async def test_cleanup_reads_only_the_executors_task(setup, registered):
    """Alice's expired run remains recoverable while Bob's persisted task is active."""
    record, name, _ = registered
    task = make_task(setup, compile_name=name, record=record, user_id="bob")
    tasks = setup.tracker._store = PersistentTaskStore(_FakeAgfs())
    task.status = TaskStatus.RUNNING
    await tasks.create(task)
    run = dict(new_run(task), status="completed", finished_at=100)
    await setup.store.put_run("acme", "alice", name, run)

    reads = tasks._agfs.read = AsyncMock(wraps=tasks._agfs.read)
    listings = tasks._agfs.ls = AsyncMock(wraps=tasks._agfs.ls)
    assert await setup.lifecycle.delete_expired(now=8 * 24 * 3600, limit=10) == 0
    assert await setup.store.get_run("acme", "alice", name, task.task_id) == run
    reads.assert_awaited_once_with(f"/local/acme/_system/tasks/bob/{task.task_id}.json")
    listings.assert_not_awaited()


async def test_cleanup_resumes_past_new_runs_and_retains_pending_recovery(setup):
    record = new_record(PARAMS)
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    tasks = []
    for created in (100, 200, 300):
        task = make_task(setup, compile_name=compile_name, record=record, created_at=created)
        await setup.lifecycle.record_queued(task)
        run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
        run.update(status="completed", finished_at=created)
        await setup.store.put_run("acme", "alice", compile_name, run)
        setup.tracker.tasks[task.task_id].status = TaskStatus.COMPLETED
        tasks.append(task)
    # The oldest run still owns unsettled Work and must remain available for recovery.
    setup.tracker.tasks[tasks[0].task_id].status = TaskStatus.RUNNING
    now = 7 * 24 * 3600 + 250
    reads = setup.files.read = AsyncMock(wraps=setup.files.read)
    removed = 0
    for checked in range(1, len(tasks) + 1):
        removed += await setup.lifecycle.delete_expired(now=now, limit=1)
        paths = [call.args[0] for call in reads.await_args_list if "/runs/" in call.args[0]]
        assert len(paths) == checked + removed  # Conditional deletion re-reads the selected run.
        assert len(set(paths)) == checked
    assert removed == 1
    other_id = await setup.store.create("acme", "bob", record, compile_name="wiki-other")
    old_run = dict(run, finished_at=100)
    await setup.store.put_run("acme", "bob", other_id, old_run)
    assert await setup.lifecycle.delete_expired(now=now, limit=10) == 0
    assert await setup.lifecycle.delete_expired(now=now, limit=10) == 1
    assert await setup.store.list_runs("acme", "bob", other_id) == []
    remaining = await setup.store.list_runs("acme", "alice", compile_name)
    assert [run["created_at"] for run in remaining] == [300, 100]
    assert (await setup.store.get("acme", "alice", compile_name))["last_run_time"] == 300


async def test_cancel_before_output_snapshot_rolls_back_without_advancing_progress(
    setup, registered
):
    record, compile_name, task = registered
    await setup.lifecycle.on_start(task)

    acquire = setup.files.pathlock_acquire_tree

    async def cancel_before_snapshot(*args, **kwargs):
        lease = await acquire(*args, **kwargs)
        setup.tracker.cancelled.add(task.task_id)
        return lease

    setup.files.pathlock_acquire_tree = cancel_before_snapshot
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert result.status == "cancelled"
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == {}
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["status"] == "cancelled"
    assert run["meta"]["finish_snapshot"] is not None
    assert run["result"] is None


async def test_registration_failure_does_not_publish_configuration(setup):
    compile_name = "scheduled-wiki"
    with pytest.raises(RuntimeError, match="registration failed"):
        await publish_config(
            setup.lifecycle,
            compile_name,
            AsyncMock(side_effect=RuntimeError("registration failed")),
        )
    assert await setup.store.get("acme", "alice", compile_name) is None
    assert not setup.files.files


@pytest.mark.parametrize(
    ("cancel_at", "baseline", "partial_delivery"),
    [
        ("publication", "empty", False),
        ("finished", "empty", False),
        ("outcome", "empty", False),
        ("finished", "complete", False),
        ("outcome", "partial", False),
        ("finished", "complete", True),
        ("outcome", "partial", True),
    ],
)
async def test_cancel_after_publication_still_restores_the_start_snapshot(
    setup,
    registered,
    compile_metric_events,
    monkeypatch,
    checkpoint_outage,
    state_outage,
    cancel_at,
    baseline,
    partial_delivery,
):
    record, compile_name, task = registered
    if baseline != "empty":
        async with setup.store.edit("acme", "alice", compile_name) as record:
            record["progress"] = {
                "source_commit": "a" * 40,
                "processed_roots": [SOURCE],
                "instruction": None,
                "model_name": "",
                "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
            }
            if baseline == "partial":
                record["execution_context"]["partial_compile"] = {
                    "base": "a" * 40,
                    "signature": execution_signature(record),
                }
        task = make_task(setup, compile_name=compile_name, record=record)
    before = deepcopy(record)
    setup.fs.source_changes = [{"uri": SOURCE + "/source.md", "change_type": "added"}]
    await setup.lifecycle.on_start(task)
    original_diff = setup.fs.diff_paths

    async def cancel_after_publication(*, dirs, **kwargs):
        if cancel_at == "publication" and dirs == [TARGET] and len(setup.fs.commits) == 2:
            setup.tracker.cancelled.add(task.task_id)
        run = await setup.lifecycle._read_run(task)
        if dirs == [SOURCE] and kwargs["base"] == run["meta"]["start_snapshot"]:
            return []
        return await original_diff(dirs=dirs, **kwargs)

    monkeypatch.setattr(setup.fs, "diff_paths", cancel_after_publication)
    terminal = ExternalTaskSnapshot(
        status="completed", result={"completion_status": "partial"} if partial_delivery else None
    )
    if cancel_at != "publication":
        setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
        checkpoint_outage(
            lambda checkpoint: (
                checkpoint.get("phase") == "finished"
                if cancel_at == "finished"
                else checkpoint.get("outcome") is not None
            )
        )
        with pytest.raises(OSError, match="checkpoint unavailable"):
            await setup.lifecycle.on_finish(task, terminal)
        published = await setup.store.get("acme", "alice", compile_name)
        if not partial_delivery:
            run = await setup.lifecycle._read_run(task)
            assert published["progress"]["source_commit"] == run["meta"]["start_snapshot"]
        assert not setup.tracker.tasks[task.task_id].auth["compile_execution"].get("outcome")
        setup.tracker.cancelled.add(task.task_id)

        # A failed compensation write retries without restoring the output a second time.
        if any(published[key] != before[key] for key in ("progress", "execution_context")):
            state_outage(lambda state: True)
            with pytest.raises(OSError, match="state unavailable"):
                await CompileLifecycle(setup.store, setup.fs).on_finish(deepcopy(task), terminal)
            assert (await setup.store.get("acme", "alice", compile_name))["progress"] == published[
                "progress"
            ]
        if cancel_at == "outcome":
            checkpoint_outage(
                lambda checkpoint: (checkpoint.get("outcome") or {}).get("status") == "cancelled"
            )
            with pytest.raises(OSError, match="checkpoint unavailable"):
                await CompileLifecycle(setup.store, setup.fs).on_finish(deepcopy(task), terminal)
    restarted = CompileLifecycle(setup.store, setup.fs)
    result = await restarted.on_finish(deepcopy(task), terminal)
    assert result.status == "cancelled"
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    run = await restarted._read_run(resumed)
    assert run["meta"]["finish_snapshot"] == "f" * 40
    assert run["result"] is None
    restored = await setup.store.get("acme", "alice", compile_name)
    assert restored["progress"] == before["progress"]
    assert restored["execution_context"] == before["execution_context"]
    # A replay sees a completed rollback and cannot create further commits.
    await restarted.on_finish(resumed, ExternalTaskSnapshot(status="cancelled"))
    setup.fs.restore.assert_awaited_once()
    assert len(setup.fs.commits) == 3
    assert len(compile_metric_events) == (0 if cancel_at == "publication" else 1)
    next_task = make_task(setup, compile_name=compile_name, record=restored, created_at=200)
    assert await restarted.on_start(next_task) == "proceed"


@pytest.mark.parametrize(
    "change", ["progress", "partial_compile", "inputs", "replacement", "unrelated", "deleted"]
)
async def test_baseline_compensation_preserves_later_configuration(
    setup, registered, checkpoint_outage, change
):
    """Cancellation only undoes the baseline owned by this run."""
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    checkpoint_outage(lambda checkpoint: checkpoint.get("phase") == "finished")
    with pytest.raises(OSError, match="checkpoint unavailable"):
        await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    published = await setup.store.get("acme", "alice", name)
    if change in {"replacement", "deleted"}:
        setup.files.files.pop(setup.store._record_path("acme", "alice", name))
        if change == "replacement":
            replacement = new_record(PARAMS)
            replacement["progress"] = published["progress"]
            await setup.store.create("acme", "alice", replacement, compile_name=name)
    else:
        async with setup.store.edit("acme", "alice", name) as record:
            if change == "progress":
                record["progress"]["source_commit"] = "b" * 40
            elif change == "partial_compile":
                record["execution_context"]["partial_compile"] = {"base": "b" * 40}
            elif change == "inputs":
                record["instruction"] = "new instruction"
            else:
                record["execution_context"]["new_context"] = "preserved"
    expected = await setup.store.get("acme", "alice", name)
    if change == "unrelated":
        expected["progress"] = {}
    setup.tracker.cancelled.add(task.task_id)
    result = await CompileLifecycle(setup.store, setup.fs).on_finish(
        deepcopy(task), ExternalTaskSnapshot(status="completed")
    )
    assert result.status == "cancelled"
    assert await setup.store.get("acme", "alice", name) == expected
    setup.fs.restore.assert_awaited_once()


async def test_creator_and_account_isolation_with_paging(setup):
    names = ["Alpha-wiki", "wiki-2", "wiki_3"]
    for owner, compile_name in zip(("bob", "alice", "bob"), names, strict=True):
        await setup.store.create("acme", owner, new_record(PARAMS), compile_name=compile_name)
    assert await setup.store.get("acme", "bob", names[1]) is None
    assert await setup.store.get("another-account", "bob", names[1]) is None
    await setup.store.create("another-account", "bob", new_record(PARAMS), compile_name=names[1])
    assert (await setup.store.get("acme", "alice", names[1]))["execution_context"][
        "owner_user_id"
    ] == "alice"
    assert (await setup.store.get("another-account", "bob", names[1]))["execution_context"][
        "owner_user_id"
    ] == "bob"
    all_records = [record async for record in setup.store.iter_records("acme")]
    assert [compile_name for compile_name, _ in all_records] == names
    assert [
        compile_name async for compile_name, _ in setup.store.iter_records("acme", cursor=names[0])
    ] == names[1:]
    assert [
        compile_name async for compile_name, _ in setup.store.iter_records("acme", user_id="alice")
    ] == [names[1]]
    for owner in ("..", "../bob", "/bob", "", None):
        with pytest.raises(InvalidArgumentError):
            await setup.store.get("acme", owner, names[0])
    with pytest.raises(InvalidArgumentError, match="owner cannot change"):
        async with setup.store.edit("acme", "alice", names[1]) as record:
            record["execution_context"]["owner_user_id"] = "bob"


@pytest.mark.parametrize("run_exists", [False, True])
async def test_cancel_before_start_repairs_registration(setup, registered, run_exists):
    """Cancellation without a start repairs missing runs and unpersisted acceptance times."""
    record, compile_name, task = registered
    if run_exists:
        await setup.store.put_run("acme", "alice", compile_name, new_run(task))

    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="cancelled"))

    assert result.status == "cancelled"
    state = await setup.store.get("acme", "alice", compile_name)
    assert state["task_first_create_time"] == record["task_first_create_time"]
    assert state["last_run_time"] == task.created_at
    assert state["progress"] == {}
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["status"] == "cancelled"
    assert run["started_at"] is None
    assert run["finished_at"] is not None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_prepared_finalization_replays_with_empty_auth(setup, registered, status):
    """A durable preparation checkpoint avoids acceptance reads even with stale worker auth."""
    _, compile_name, task = registered
    await setup.lifecycle.on_start(task)
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    task.auth = {}

    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status=status))

    assert result.status == status
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["status"] == status and run["finished_at"] is not None

    restarted = CompileLifecycle(setup.store, setup.fs)
    result = await restarted.on_finish(task, ExternalTaskSnapshot(status=status))
    assert result.status == status
    assert await setup.store.get_run("acme", "alice", compile_name, task.task_id) == run


@pytest.mark.parametrize("failure", ["undo", "state"])
async def test_progress_publication_recovers_after_state_write_failure(
    setup, registered, checkpoint_outage, state_outage, failure
):
    """A run retries baseline publication after a checkpoint or configuration write fails."""
    _, compile_name, task = registered
    await setup.lifecycle.on_start(task)
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    if failure == "undo":
        checkpoint_outage(lambda checkpoint: checkpoint.get("progress_rollback"))
    else:
        state_outage(lambda state: state["progress"])
    with pytest.raises(OSError, match="unavailable"):
        await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == {}
    assert setup.tracker.tasks[task.task_id].auth["compile_execution"]["phase"] != "finished"
    undo = deepcopy(
        setup.tracker.tasks[task.task_id].auth["compile_execution"].get("progress_rollback")
    )
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    result = await CompileLifecycle(setup.store, setup.fs).on_finish(
        resumed, ExternalTaskSnapshot(status="completed")
    )
    assert result.status == "completed"
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert (await setup.store.get("acme", "alice", compile_name))["progress"][
        "source_commit"
    ] == run["meta"]["start_snapshot"]
    saved_undo = resumed.auth["compile_execution"]["progress_rollback"]
    assert saved_undo["before"] == {"progress": {}, "partial_compile": None}
    if failure == "state":
        assert saved_undo == undo


@pytest.mark.parametrize("lock_failures", [1, 3])
async def test_submit_exhaustion_skips_rollback(setup, registered, monkeypatch, lock_failures):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    setup.files.pathlock_acquire_tree = AsyncMock()
    lock_error = LockAcquisitionError("Compile admission is locked")
    acquire = setup.files.pathlock_acquire_exact
    failures_left = lock_failures

    async def acquire_lock(path, **kwargs):
        nonlocal failures_left
        if failures_left and "/task_locks/compile/" in path:
            failures_left -= 1
            raise lock_error
        return await acquire(path, **kwargs)

    monkeypatch.setattr(setup.files, "pathlock_acquire_exact", acquire_lock)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    snapshot = ExternalTaskSnapshot(
        status="failed", stage=SUBMIT_EXHAUSTED, error_message="ConnectTimeout"
    )
    result = await setup.lifecycle.on_finish(task, snapshot)
    run = await setup.store.get_run("acme", "alice", name, task.task_id)
    assert result.status == "failed"
    if lock_failures == 3:
        assert result == snapshot
        assert run["status"] == "running" and run["finished_at"] is None
    else:
        assert run["status"] == "failed"
        assert run["error"] == "ConnectTimeout" and run["finished_at"] is not None
    setup.files.pathlock_acquire_tree.assert_not_awaited()
    setup.fs.restore.assert_not_awaited()
    assert setup.fs.commits == [[TARGET, SOURCE]]


@pytest.mark.parametrize("status,scheduling_fails", [("failed", False), ("cancelled", True)])
async def test_rollback_reindexes_without_waiting(setup, registered, status, scheduling_fails):
    """Rollback schedules changed paths, falling back to vector rebuild on scheduling errors."""
    _, _, task = registered
    await setup.lifecycle.on_start(task)
    restored = ["resources/output/page.md"]
    deleted = ["resources/output/removed.md"]
    setup.fs.restore.return_value.update(written_paths=restored, deleted_paths=deleted)
    setup.fs._schedule_restore_reindex_for_paths.return_value = "reindex-1"
    if scheduling_fails:
        setup.fs._schedule_restore_reindex_for_paths.side_effect = OSError("index unavailable")
    setup.tracker.wait = AsyncMock(side_effect=AssertionError("Rollback must not wait for indexes"))

    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status=status))
    assert result.status == status
    reindex = setup.fs._schedule_restore_reindex_for_paths
    reindex.assert_awaited_once()
    ctx = reindex.await_args.kwargs["real_ctx"]
    reindex.assert_awaited_once_with(
        written_paths=restored, deleted_paths=deleted, project_dir=TARGET, real_ctx=ctx
    )
    if scheduling_fails:
        setup.fs._schedule_vector_rebuild.assert_called_once_with(
            written=restored, deleted=deleted, ctx=ctx
        )
    else:
        setup.fs._schedule_vector_rebuild.assert_not_called()
    setup.tracker.wait.assert_not_awaited()


@pytest.mark.parametrize("conflict", ["output", "admission", "cancelled", "history"])
async def test_rollback_lock_exhaustion_fails_task(setup, registered, monkeypatch, conflict):
    cancelled = conflict == "cancelled"
    template = make_task(setup) if cancelled else registered[2]
    owner = {"account_id": template.account_id, "user_id": template.user_id}
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    for module in ("task_tracker", "external_task_service"):
        monkeypatch.setattr(f"openviking.service.{module}.get_task_tracker", lambda: tracker)
    task = await tracker.create("compile", task_id=template.task_id, meta=template.meta, **owner)
    await tracker.start(task.task_id, **owner)
    await tracker.register_work(task.task_id, "work", "ExternalTask", **owner)
    await tracker.start_work(task.task_id, "work", queue_name="ExternalTask", **owner)
    await setup.lifecycle.on_start(task)
    baseline = (await setup.lifecycle._read_run(task))["meta"]["start_snapshot"]
    if cancelled:
        await tracker.cancel(task.task_id, **owner)
    error = ValueError("invalid lock request: pathlock lease expired during acquisition")
    history_locked = conflict == "history"
    if conflict == "history":
        acquire = setup.files.pathlock_acquire_exact
        meta = template.meta["request"]["compile"]
        finish_lock = (
            setup.store._run_path(
                template.account_id, meta["owner_user_id"], meta["compile_name"], task.task_id
            )
            + ".finish"
        )

        async def acquire_with_conflict(path, **kwargs):
            if history_locked and path == finish_lock:
                raise error
            return await acquire(path, **kwargs)

        monkeypatch.setattr(setup.files, "pathlock_acquire_exact", acquire_with_conflict)
    elif conflict == "admission":
        monkeypatch.setattr(setup.lifecycle, "_admission", Mock(side_effect=error))
    else:
        monkeypatch.setattr(setup.files, "pathlock_acquire_tree", AsyncMock(side_effect=error))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    external = ExternalTaskService()
    provider = CompileService(Mock(), external, setup.fs)
    provider.configure_lifecycle(setup.lifecycle)
    external.register(provider)
    terminal = ExternalTaskSnapshot(
        status="cancelled" if cancelled else "failed",
        error_code=None if cancelled else "RUNTIME_FAILED",
    )
    publish = partial(external._apply_snapshot, terminal, task_id=task.task_id, auth={}, **owner)
    for _ in range(60):
        assert not await publish()
    if conflict == "history":
        assert not await publish()
        assert (await setup.lifecycle._read_run(task))["finished_at"] is None
        assert not (await tracker.get(task.task_id, **owner)).is_terminal()
        # Resume from durable state with the output available but history still locked.
        provider.configure_lifecycle(CompileLifecycle(setup.store, setup.fs))
        monkeypatch.setattr(provider._lifecycle, "_admission", Mock(side_effect=AssertionError))
        assert not await publish()
        history_locked = False
        with monkeypatch.context() as patch:
            patch.setattr(setup.store, "update_run", AsyncMock(side_effect=error))
            assert not await publish()
            assert (await setup.lifecycle._read_run(task))["finished_at"] is None
    with monkeypatch.context() as patch:
        save_failure = AsyncMock(side_effect=OSError("Task write unavailable"))
        patch.setattr(tracker, "fail", save_failure)
        with pytest.raises(ExternalTaskResultSaveError):
            await publish()
    assert await publish()
    await tracker.mark_work_done(task.task_id, "work", **owner)
    failed = await tracker.get(task.task_id, **owner)
    assert failed.status is TaskStatus.FAILED and failed.meta["rollback_lock_retries"] == 60
    assert failed.result == {"rollback_incomplete": True, "start_snapshot": baseline}
    assert failed.error == save_failure.await_args.args[1]
    assert failed.error.startswith("COMPILE_ROLLBACK_LOCK_EXHAUSTED:")
    if not cancelled:
        run = await setup.store.get_run("acme", "alice", registered[1], task.task_id)
        assert run["status"] == "failed"
        assert run["finished_at"] is not None and run["result"] == failed.result
