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
from openviking.service.compile_targets import (
    CompileLifecycle,
    CompileStateStore,
    execution_signature,
    new_record,
    run_view,
)
from openviking.service.external_task_service import (
    SUBMIT_EXHAUSTED,
    ExternalTaskError,
    ExternalTaskResultSaveError,
    ExternalTaskService,
    ExternalTaskSnapshot,
)
from openviking.service.task_domain import TaskPatch, TaskRecord, TaskRetentionPolicy, TaskStatus
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.utils.compile_recovery import source_set_digest
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, PermissionDeniedError
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
        task.meta.update(deepcopy(patch.meta or {}))
        if patch.stage is not None:
            task.stage = patch.stage
        return SimpleNamespace(accepted=True)

    async def list_tasks(
        self, *, account_id, task_type, resource_id, task_id=None, before=None, limit=None
    ):
        tasks = [
            deepcopy(task)
            for task in self.tasks.values()
            if task.account_id == account_id
            and task.task_type == task_type
            and task.resource_id == resource_id
            and (task_id is None or task.task_id == task_id)
            and (before is None or (task.created_at, task.task_id) < before)
        ]
        tasks.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
        return tasks[:limit]

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
        resource_id=str(record.get("config_id", "")) if record else SOURCE,
        started_at=created_at + 1,
        status=TaskStatus.RUNNING,
        meta={
            "request": request,
            "compile": {
                "trigger": "initial"
                if record and record["last_run_time"] is None and trigger == "manual"
                else trigger,
            },
        },
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
    outcome = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="failed"))
    task.status = TaskStatus(outcome.status)
    task.finished_at = task.started_at + 1
    run = run_view(task)
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
        if run["status"] == "completed":
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


async def test_case_insensitive_storage_preserves_configuration_names():
    files = CaseInsensitiveFiles()
    store = make_store(files)
    names = ["Research", "Wiki", "research", "研发-A", "研发-a", "𠀀" * 32]
    for name in names:
        record = new_record({**PARAMS, "instruction": name})
        await store.create("acme", "alice", record, compile_name=name)
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
    service = CompileService(Mock(), Mock(), setup.fs)
    service.configure_lifecycle(setup.lifecycle)
    ctx = SimpleNamespace(account_id="acme", user=SimpleNamespace(user_id="alice"))
    setup.fs._ensure_access = AsyncMock()
    assert await service.list_runs("missing", ctx, limit=10, cursor=None) == {
        "runs": [],
        "next_cursor": None,
    }
    delayed.meta["compile"]["trigger"] = "manual"
    setup.tracker.tasks[delayed.task_id] = deepcopy(delayed)
    runs = (await service.list_runs(compile_name, ctx, limit=10, cursor=None))["runs"]
    assert [run["meta"]["trigger"] for run in runs] == ["scheduled", "manual", "initial"]
    older = (await service.list_runs(compile_name, ctx, limit=10, cursor=delayed.task_id))["runs"]
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
    run = await setup.lifecycle._read_run(second)
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
    run = await resumed._read_run(stale_task)
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
    outcome = await setup.lifecycle.on_finish(task, snapshot)
    run = await setup.lifecycle._read_run(task)
    assert run["status"] == status
    assert run["meta"]["trigger"] == "initial"
    assert run["meta"]["finish_snapshot"] is not None
    setup.fs.restore.assert_awaited_once()
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == record["progress"]
    assert "secret_value" not in run["error"] and "bearer-secret" not in run["error"]
    assert "[REDACTED]" in run["error"]
    assert outcome.error_message == run["error"]
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


@pytest.mark.parametrize("hook", ["on_start", "_refresh_checkpoint"])
async def test_checkpoint_refresh_reads_only_its_task_despite_stale_cache(setup, hook):
    files = _FakeAgfs()
    reader = setup.tracker._store = PersistentTaskStore(files)
    writer = PersistentTaskStore(files)
    task = make_task(setup)
    owner = {"account_id": task.account_id, "user_id": task.user_id}
    task.auth = {"compile_execution": {"phase": "running"}}
    await reader.create(task)
    for i in range(99):
        other = TaskRecord(f"write-{i}", "content_write", account_id="acme", user_id="bob")
        await writer.create(other)
    latest = {"compile_execution": {"phase": "committing"}}
    await writer.patch_task(task.task_id, TaskPatch(auth=latest), **owner)

    (current,) = await reader.list_durable(task.account_id, task_type="compile")
    assert current.auth == latest
    assert (await reader.get_task(task.task_id, **owner)).auth == task.auth

    reads = reader._agfs.read = AsyncMock(wraps=reader._agfs.read)
    listings = reader._agfs.ls = AsyncMock(wraps=reader._agfs.ls)
    task.auth["external_task_id"] = "runtime-session"
    await getattr(setup.lifecycle, hook)(task)

    reads.assert_awaited_once_with(f"/local/acme/_system/tasks/alice/{task.task_id}.json")
    listings.assert_not_awaited()
    assert task.auth == {**latest, "external_task_id": "runtime-session"}


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


async def test_history_reads_the_executors_task(setup, registered):
    """The configuration owner can read an execution by another user."""
    record, name, _ = registered
    task = make_task(setup, compile_name=name, record=record, user_id="bob")
    tasks = setup.tracker._store = PersistentTaskStore(_FakeAgfs())
    task.status = TaskStatus.RUNNING
    await tasks.create(task)
    tracker = TaskTracker(tasks)
    setup.tracker.list_tasks = tracker.list_tasks

    listings = tasks._agfs.ls = AsyncMock(wraps=tasks._agfs.ls)
    assert await setup.lifecycle.get_run("acme", "alice", name, task.task_id) == run_view(task)
    assert await setup.lifecycle.get_run("acme", "bob", name, task.task_id) is None
    listings.assert_not_awaited()


async def test_task_cleanup_retains_compile_history_for_seven_days(setup, registered):
    record, compile_name, _ = registered
    store = PersistentTaskStore(_FakeAgfs())
    policy = TaskRetentionPolicy(24 * 3600, 7 * 24 * 3600)
    tasks = []
    for created in (100, 200, 300):
        task = make_task(setup, compile_name=compile_name, record=record, created_at=created)
        task.status = TaskStatus.COMPLETED
        task.updated_at = task.finished_at = created
        if created == 100:
            task.status = TaskStatus.RUNNING
        elif created == 300:
            task.status = TaskStatus.CANCELLED
        await store.create(task)
        tasks.append(task)
    assert (await store.delete_expired(policy, now=2 * 24 * 3600, limit=10)).deleted_count == 0
    now = 7 * 24 * 3600 + 250
    results = [await store.delete_expired(policy, now=now, limit=1) for _ in range(4)]
    assert sum(result.deleted_count for result in results) == 1
    remaining = await store.list("acme", task_type="compile", limit=None)
    assert [task.created_at for task in remaining] == [300, 100]
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == {}


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
    run = await setup.lifecycle._read_run(task)
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
        if dirs == [SOURCE] and kwargs["base"] == task.meta["compile"]["start_snapshot"]:
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
            assert published["progress"]["source_commit"] == task.meta["compile"]["start_snapshot"]
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


async def test_initial_run_uses_config_state_without_listing_tasks(setup, registered, monkeypatch):
    """Distinct tasks with the same creation time share one initial run per configuration."""
    record, compile_name, first = registered
    second = make_task(
        setup,
        compile_name=compile_name,
        record=record,
        created_at=first.created_at,
        trigger="scheduled",
    )
    list_tasks = AsyncMock(side_effect=AssertionError("Unexpected account-wide task scan"))
    monkeypatch.setattr(setup.tracker, "list_durable", list_tasks)

    await setup.lifecycle.record_queued(first)
    await setup.lifecycle.record_queued(second)
    await setup.lifecycle.record_queued(first)

    initial = run_view(first)
    scheduled = run_view(second)
    assert initial["meta"]["trigger"] == "initial"
    assert scheduled["meta"]["trigger"] == "scheduled"
    state = await setup.store.get("acme", "alice", compile_name)
    assert state["task_first_create_time"] == record["task_first_create_time"]
    list_tasks.assert_not_awaited()


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


async def test_cancel_before_start_is_visible_in_task_history(setup, registered, monkeypatch):
    """Queued cancellation publishes Task history without running the snapshot lifecycle."""
    record, compile_name, task = registered
    task.status = TaskStatus.PENDING
    task.started_at = None
    task.stage = "queued"
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    await tracker._store.create(task)
    await setup.lifecycle.record_queued(task)
    await tracker.cancel(task.task_id, account_id="acme", user_id="alice")
    state = await setup.store.get("acme", "alice", compile_name)
    assert state["task_first_create_time"] == record["task_first_create_time"]
    assert state["last_run_time"] == task.created_at
    assert state["progress"] == {}
    run = await setup.lifecycle.get_run("acme", "alice", compile_name, task.task_id)
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
    run = await setup.lifecycle._read_run(task)
    assert run["status"] == status
    assert task.result is None and task.finished_at is None

    restarted = CompileLifecycle(setup.store, setup.fs)
    result = await restarted.on_finish(task, ExternalTaskSnapshot(status=status))
    assert result.status == status
    assert await restarted._read_run(task) == run


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
    run = await CompileLifecycle(setup.store, setup.fs)._read_run(resumed)
    assert (await setup.store.get("acme", "alice", compile_name))["progress"][
        "source_commit"
    ] == run["meta"]["start_snapshot"]
    saved_undo = resumed.auth["compile_execution"]["progress_rollback"]
    assert saved_undo["before"] == {"progress": {}, "partial_compile": None}
    if failure == "state":
        assert saved_undo == undo


@pytest.mark.parametrize("lock_failures", [0, 1, 2, 3])
async def test_submit_exhaustion_skips_rollback(setup, registered, monkeypatch, lock_failures):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    setup.files.pathlock_acquire_tree = AsyncMock()
    lock_error = LockAcquisitionError("Compile admission is locked")
    lock_run = Mock(side_effect=[lock_error] * lock_failures + [setup.lifecycle._admission(task)])
    monkeypatch.setattr(setup.lifecycle, "_admission", lock_run)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    snapshot = ExternalTaskSnapshot(
        status="failed", stage=SUBMIT_EXHAUSTED, error_message="ConnectTimeout"
    )
    result = await setup.lifecycle.on_finish(task, snapshot)
    run = await setup.lifecycle._read_run(task)
    assert result.status == "failed"
    assert lock_run.call_count == min(lock_failures + 1, 3)
    assert [call.args[0] for call in sleep.await_args_list] == [1, 3][:lock_failures]
    if lock_failures == 3:
        assert result == snapshot
        assert run["status"] == "running" and run["finished_at"] is None
    else:
        assert run["status"] == "failed"
        assert run["error"] == "ConnectTimeout"
    setup.files.pathlock_acquire_tree.assert_not_awaited()
    setup.fs.restore.assert_not_awaited()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("scheduling_fails", [False, True])
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


@pytest.mark.parametrize("conflict", ["output", "admission", "cancelled"])
async def test_rollback_lock_exhaustion_fails_task(setup, registered, monkeypatch, conflict):
    cancelled = conflict == "cancelled"
    template = make_task(setup) if cancelled else registered[2]
    owner = {"account_id": template.account_id, "user_id": template.user_id}
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    for module in ("task_tracker", "external_task_service"):
        monkeypatch.setattr(f"openviking.service.{module}.get_task_tracker", lambda: tracker)
    task = await tracker.create(
        "compile",
        task_id=template.task_id,
        meta=template.meta,
        resource_id=template.resource_id,
        **owner,
    )
    await tracker.start(task.task_id, **owner)
    await tracker.register_work(task.task_id, "work", "ExternalTask", **owner)
    await tracker.start_work(task.task_id, "work", queue_name="ExternalTask", **owner)
    await setup.lifecycle.on_start(task)
    baseline = (await setup.lifecycle._read_run(task))["meta"]["start_snapshot"]
    if cancelled:
        await tracker.cancel(task.task_id, **owner)
    error = ValueError("invalid lock request: pathlock lease expired during acquisition")
    admission = setup.lifecycle._admission
    if conflict == "admission":
        monkeypatch.setattr(setup.lifecycle, "_admission", Mock(side_effect=error))
    else:
        monkeypatch.setattr(setup.files, "pathlock_acquire_tree", AsyncMock(side_effect=error))
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
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
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1, 2, 4, 8, 16] + [30] * 54
    if conflict == "admission":
        assert not await publish()
        assert (await setup.lifecycle._read_run(task))["finished_at"] is None
        assert not (await tracker.get(task.task_id, **owner)).is_terminal()
        # The exhausted outcome still requires the target admission mutex.
        provider.configure_lifecycle(CompileLifecycle(setup.store, setup.fs))
        monkeypatch.setattr(provider._lifecycle, "_admission", Mock(side_effect=error))
        assert not await publish()
        monkeypatch.setattr(provider._lifecycle, "_admission", admission)
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
        run = await setup.lifecycle.get_run("acme", "alice", registered[1], task.task_id)
        assert run["status"] == "failed"
        assert run["finished_at"] is not None and run["result"] == failed.result
