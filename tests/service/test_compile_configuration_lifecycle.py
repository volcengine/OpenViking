"""Configuration, run, and recovery contracts for registered and transient Compile."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openviking.service.compile_targets import (
    CompileLifecycle,
    CompileStateStore,
    execution_signature,
    new_record,
    split_control_args,
)
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_domain import TaskPatch, TaskRecord, TaskStatus
from openviking.service.task_store import PersistentTaskStore
from openviking.storage.errors import ResourceBusyError
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
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
        self.source_changes = []
        self.output_changes = []
        self._can_access_many = AsyncMock(return_value={})
        self._schedule_restore_reindex_for_paths = AsyncMock(return_value=None)
        self._schedule_vector_rebuild = AsyncMock()

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
    store._cleanup_scan = None
    store._cleanup_lock = asyncio.Lock()
    return store


@pytest.fixture
def setup(monkeypatch):
    files = AsyncFiles()
    tracker = Tracker()
    fs = SnapshotFS(files)
    store = make_store(files)
    finalize = AsyncMock(side_effect=AssertionError("Compile must not wait for target tagging"))
    find = AsyncMock(return_value=SimpleNamespace(total=0))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.server.dependencies.get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(finalize_compile_directories=finalize),
            search=SimpleNamespace(find=find),
        ),
    )
    return SimpleNamespace(
        files=files,
        tracker=tracker,
        fs=fs,
        store=store,
        lifecycle=CompileLifecycle(store, fs),
        finalize=finalize,
        find=find,
    )


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
    record = new_record(PARAMS)
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
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
    "name", ["", " wiki", "wiki ", "wiki\n", "../wiki", "a/b", "a\\b", "文档", "a" * 65, None, 1]
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
    names = ["Research", "Wiki", "research"]
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
        "",
        "ff",
        "2e2e",
        "2F",
        "61 62",
        "name_77696b69",
    ],
)
async def test_configuration_discovery_ignores_noncanonical_directories(setup, segment):
    setup.files.files[f"/local/acme/_system/compile/tasks/alice/{segment}/state.json"] = b"{}"
    assert [name async for name, _ in setup.store.iter_records("acme")] == []


async def test_creation_locks_state_through_schedule_registration(setup):
    registered = asyncio.Event()
    publish = asyncio.Event()

    async def register():
        registered.set()
        await publish.wait()

    async def callback():
        async with setup.store.edit("acme", "alice", "wiki") as record:
            return record["to"]

    first = asyncio.create_task(
        setup.store.create(
            "acme", "alice", new_record(PARAMS), compile_name="wiki", before_publish=register
        )
    )
    await registered.wait()
    delivery = asyncio.create_task(callback())
    duplicate_registration = AsyncMock()
    second = asyncio.create_task(
        make_store(setup.files).create(
            "acme",
            "alice",
            new_record(PARAMS),
            compile_name="wiki",
            before_publish=duplicate_registration,
        )
    )
    await asyncio.sleep(0)
    assert not second.done() and not delivery.done()
    publish.set()
    assert await first == "wiki"
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
    assert current["task_first_create_time"] == 100
    assert current["last_run_time"] == 300
    runs = await setup.store.list_runs("acme", "alice", compile_name, limit=2)
    assert [run["created_at"] for run in runs] == [300, 200]
    assert (await setup.store.list_runs("acme", "alice", compile_name, cursor=runs[-1]["task_id"]))[
        0
    ]["created_at"] == 100
    runs = await setup.store.list_runs("acme", "alice", compile_name)
    assert [run["meta"]["trigger"] for run in runs] == ["scheduled", "manual", "initial"]


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


async def test_manual_and_scheduled_runs_share_progress_and_ignore_stale_completion(
    setup, registered
):
    record, compile_name, first = registered
    await setup.lifecycle.record_queued(first)
    assert await setup.lifecycle.on_start(first) == "proceed"
    first_plan = await setup.lifecycle.runtime_changes(
        first.meta["request"], "acme", first.task_id, "alice"
    )
    assert first_plan["execution"]["mode"] == "full"
    setup.find.side_effect = [SimpleNamespace(total=7), SimpleNamespace(total=2)]
    await setup.lifecycle.on_finish(first, ExternalTaskSnapshot(status="completed"))
    state = await setup.store.get("acme", "alice", compile_name)
    assert state["progress"]["source_commit"] == first_plan["head"]
    second = make_task(
        setup, compile_name=compile_name, record=state, created_at=200, trigger="scheduled"
    )
    await setup.lifecycle.record_queued(second)
    outcome = await setup.lifecycle.on_start(second)
    assert isinstance(outcome, ExternalTaskSnapshot) and outcome.status == "completed"
    await setup.lifecycle.on_finish(second, outcome)
    run = await setup.store.get_run("acme", "alice", compile_name, second.task_id)
    await CompileLifecycle(setup.store, setup.fs).on_finish(
        first, ExternalTaskSnapshot(status="completed")
    )
    assert (await setup.store.get("acme", "alice", compile_name))["conflict_num"] == 2
    assert setup.find.await_count == 2
    assert run["meta"]["trigger"] == "scheduled"
    assert run["meta"]["base_snapshot"] == first_plan["head"]
    assert run["meta"]["finish_snapshot"] == run["meta"]["start_snapshot"]
    assert len(setup.fs.commits) == 3
    assert (await setup.store.get("acme", "alice", compile_name))["progress"][
        "source_commit"
    ] == run["meta"]["start_snapshot"]


async def test_changes_new_roots_and_updated_configuration_do_not_rewrite_frozen_inputs(setup):
    record = new_record(PARAMS)
    record["progress"] = {
        "source_commit": "a" * 40,
        "processed_roots": [SOURCE],
        "instruction": None,
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
        current["to"] = "viking://resources/new-target"
        current["progress"] = {}
    resumed = CompileLifecycle(setup.store, setup.fs)
    stale_task = deepcopy(setup.tracker.tasks[task.task_id])
    stale_task.auth = {}
    assert await resumed.on_start(stale_task) == "proceed"
    assert (
        await resumed.runtime_changes(task.meta["request"], "acme", task.task_id, "alice") == plan
    )
    assert len(setup.fs.commits) == 1
    await resumed.on_finish(stale_task, ExternalTaskSnapshot(status="completed"))
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == {}
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["params"]["instruction"] is None
    assert (await setup.store.get("acme", "alice", compile_name))["conflict_num"] is None


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_unsuccessful_runs_preserve_baseline_and_redact_persisted_errors(
    setup, status, compile_metric_events
):
    record = new_record(PARAMS)
    record["progress"] = {
        "source_commit": "a" * 40,
        "processed_roots": [SOURCE],
        "instruction": None,
        "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
    }
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    setup.fs.source_changes = [{"uri": SOURCE + "/a.md", "change_type": "modified"}]
    task = make_task(setup, compile_name=compile_name, record=record)
    assert await setup.lifecycle.on_start(task) == "proceed"
    outcome = await setup.lifecycle.on_finish(
        task,
        ExternalTaskSnapshot(
            status=status,
            error_message="Bad request sk-secret_value Authorization: Bearer bearer-secret",
        ),
    )
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["status"] == status
    assert run["meta"]["trigger"] == "initial"
    assert run["meta"]["finish_snapshot"] is not None
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


@pytest.mark.parametrize("args", [{"mode": "full"}, {"last_compile_time": "now"}, {"changes": {}}])
def test_execution_mode_and_forged_plan_controls_are_rejected(args):
    with pytest.raises(InvalidArgumentError):
        split_control_args(args)


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
        removed += await setup.store.delete_expired(now=now, limit=1)
        paths = [call.args[0] for call in reads.await_args_list if "/runs/" in call.args[0]]
        assert len(paths) == checked
        assert len(set(paths)) == checked
    assert removed == 1
    other_id = await setup.store.create("acme", "bob", record, compile_name="wiki-other")
    old_run = dict(run, finished_at=100)
    await setup.store.put_run("acme", "bob", other_id, old_run)
    assert await setup.store.delete_expired(now=now, limit=10) == 0
    assert await setup.store.delete_expired(now=now, limit=10) == 1
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
        await setup.store.create(
            "acme",
            "alice",
            new_record(PARAMS),
            compile_name=compile_name,
            before_publish=AsyncMock(side_effect=RuntimeError("registration failed")),
        )
    assert await setup.store.get("acme", "alice", compile_name) is None
    assert not setup.files.files


async def test_cancel_after_publication_still_restores_the_start_snapshot(
    setup, registered, compile_metric_events
):
    record, compile_name, task = registered
    await setup.lifecycle.on_start(task)
    original_diff = setup.fs.diff_paths

    async def cancel_after_publication(*, dirs, **kwargs):
        if dirs == [TARGET] and len(setup.fs.commits) == 2:
            setup.tracker.cancelled.add(task.task_id)
        return await original_diff(dirs=dirs, **kwargs)

    setup.fs.diff_paths = cancel_after_publication
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert result.status == "cancelled"
    run = await setup.store.get_run("acme", "alice", compile_name, task.task_id)
    assert run["meta"]["finish_snapshot"] == f"{4:040x}"
    assert run["result"] is None
    assert (await setup.store.get("acme", "alice", compile_name))["progress"] == {}
    # A replay sees a completed rollback and cannot create further commits.
    await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="cancelled"))
    assert len(setup.fs.commits) == 4
    assert compile_metric_events == []


async def test_first_acceptance_discovers_a_task_whose_state_publication_was_interrupted(setup):
    record = new_record(PARAMS)
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    name = await setup.store.create("acme", "bob", record, compile_name=compile_name)
    other = make_task(
        setup, compile_name=name, record=record, owner_user_id="bob", user_id="bob", created_at=50
    )
    await setup.lifecycle.record_queued(other)
    scheduled = make_task(
        setup, compile_name=compile_name, record=record, trigger="scheduled", user_id="bob"
    )
    # The Task and run survive, but the configuration mutation does not publish.
    unpublished = deepcopy(record)
    await setup.lifecycle.record_queued(scheduled, locked_record=unpublished)
    assert (await setup.store.get("acme", "alice", compile_name))["task_first_create_time"] is None
    manual = make_task(setup, compile_name=compile_name, record=record, created_at=200)
    await setup.lifecycle.record_queued(manual)
    state = await setup.store.get("acme", "alice", compile_name)
    assert state["task_first_create_time"] == 100
    assert state["last_run_time"] == 200
    await setup.lifecycle.record_queued(scheduled)
    assert (await setup.store.get("acme", "alice", compile_name))["task_first_create_time"] == 100
    runs = await setup.store.list_runs("acme", "alice", compile_name)
    assert [run["meta"]["trigger"] for run in runs] == ["manual", "initial"]


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


@pytest.mark.parametrize("total,expected", [(0, 0), (3, 3), (100, 100), (140, 100), (None, None)])
async def test_final_conflict_count_and_search_failure(setup, total, expected):
    record = new_record(PARAMS)
    record["conflict_num"] = 9
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    task = make_task(setup, compile_name=compile_name, record=record, user_id="bob")
    setup.lifecycle.resolve_groups = AsyncMock(return_value=["reviewers"])

    async def find(**kwargs):
        setup.finalize.assert_not_awaited()
        assert len(setup.fs.commits) == 2
        if total is None:
            raise OSError("search unavailable")
        return SimpleNamespace(total=total)

    setup.find.side_effect = find
    await setup.lifecycle.on_start(task)
    assert (
        await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    ).status == "completed"
    assert (await setup.store.get("acme", "alice", compile_name))["conflict_num"] == expected
    args = setup.find.await_args.kwargs
    ctx = args.pop("ctx")
    assert (ctx.user.user_id, ctx.group_ids, ctx.bypass_acl) == ("bob", ("reviewers",), False)
    assert args == {
        "query": "",
        "target_uri": TARGET,
        "level": [2],
        "limit": 100,
        "filter": {"op": "must", "field": "search_tags", "conds": ["has_conflict=true"]},
    }


async def test_conflict_publication_recovers_after_state_write_failure(
    setup, registered, monkeypatch
):
    record, compile_name, task = registered
    setup.find.return_value = SimpleNamespace(total=9)
    await setup.lifecycle.on_start(task)
    write = setup.files.write
    fail = True

    async def write_with_outage(path, body, **kwargs):
        nonlocal fail
        if fail and path.endswith("/state.json") and json.loads(body)["conflict_num"] == 9:
            fail = False
            raise OSError("state unavailable")
        await write(path, body, **kwargs)

    monkeypatch.setattr(setup.files, "write", write_with_outage)
    with pytest.raises(OSError, match="state unavailable"):
        await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert (await setup.store.get("acme", "alice", compile_name))["conflict_num"] is None
    assert setup.tracker.tasks[task.task_id].auth["compile_execution"]["phase"] != "finished"
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    setup.find.side_effect = AssertionError("recovery must reuse the persisted statistic")
    result = await CompileLifecycle(setup.store, setup.fs).on_finish(
        resumed, ExternalTaskSnapshot(status="completed")
    )
    assert result.status == "completed"
    assert (await setup.store.get("acme", "alice", compile_name))["conflict_num"] == 9
    assert setup.find.await_count == 1


@pytest.mark.parametrize("reindex_status", [TaskStatus.COMPLETED, TaskStatus.FAILED, None])
async def test_rollback_conflict_count_waits_for_tracked_reindex(setup, registered, reindex_status):
    record, compile_name, task = registered
    await setup.lifecycle.on_start(task)
    uri = TARGET + "/page.md"
    setup.fs.output_changes = [{"uri": uri, "change_type": "modified"}]
    setup.fs._can_access_many.return_value = {uri: True}
    setup.fs.exists = AsyncMock(return_value=False)
    setup.fs.show = AsyncMock(return_value=b"restored")
    setup.fs.write_file_bytes = AsyncMock()
    setup.fs._schedule_restore_reindex_for_paths.return_value = "reindex-1"
    setup.tracker.wait = AsyncMock(
        return_value=SimpleNamespace(status=reindex_status),
        side_effect=TimeoutError() if reindex_status is None else None,
    )

    async def find(**kwargs):
        setup.tracker.wait.assert_awaited_once_with(
            "reindex-1", account_id="acme", user_id="alice", timeout=30.0
        )
        return SimpleNamespace(total=4)

    setup.find.side_effect = find
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="failed"))
    assert result.status == "failed"
    state = await setup.store.get("acme", "alice", compile_name)
    if reindex_status == TaskStatus.COMPLETED:
        assert state["conflict_num"] == 4
    else:
        assert state["conflict_num"] is None
        setup.find.assert_not_awaited()
    assert (
        setup.tracker.tasks[task.task_id].auth["compile_execution"]["rollback_reindex_task_id"]
        == "reindex-1"
    )


async def test_cancelled_unstarted_run_does_not_count_another_runs_live_target(setup):
    record = new_record(PARAMS)
    record["conflict_num"] = 4
    compile_name = await setup.store.create("acme", "alice", record, compile_name="wiki")
    running = make_task(setup, compile_name=compile_name, record=record)
    queued = make_task(setup, compile_name=compile_name, record=record, created_at=200)
    await setup.lifecycle.on_start(running)
    result = await setup.lifecycle.on_finish(queued, ExternalTaskSnapshot(status="cancelled"))
    assert result.status == "cancelled"
    assert (await setup.store.get("acme", "alice", compile_name))["conflict_num"] is None
    setup.find.assert_not_awaited()
