"""Configuration, run, and recovery contracts for registered and transient Compile."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openviking.service.compile_targets import (
    STATE_FIELDS,
    CompileLifecycle,
    CompileStateStore,
    execution_signature,
    new_record,
    split_control_args,
)
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_domain import TaskRecord, TaskStatus
from openviking.service.task_store import PersistentTaskStore
from openviking.storage.errors import ResourceBusyError
from openviking_cli.exceptions import InvalidArgumentError
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
        self._store = SimpleNamespace(list_durable=self.list_durable)
        self.cancelled = set()

    async def list_durable(self, account_id, **kwargs):
        return [deepcopy(t) for t in self.tasks.values() if t.account_id == account_id]

    async def patch_task(self, task_id, patch, **kwargs):
        task = self.tasks[task_id]
        for key, value in (patch.auth or {}).items():
            task.auth[key] = deepcopy(value)
        return SimpleNamespace(accepted=True)

    async def get_task_auth(self, task_id, **kwargs):
        return deepcopy(self.tasks[task_id].auth)

    async def is_cancellation_requested(self, task_id, **kwargs):
        return task_id in self.cancelled


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
    finalize = AsyncMock(return_value={"directory_count": 1})
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
    compile_id=None,
    record=None,
    created_at=100.0,
    trigger="manual",
    task_id=None,
    owner_user_id="alice",
    user_id="alice",
):
    request = deepcopy(PARAMS)
    request["compile"] = {"trigger": trigger, "auto_update": False, "update_schedule": None}
    if compile_id:
        request.update({key: deepcopy(record[key]) for key in PARAMS})
        request["compile"].update(
            compile_id=compile_id,
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
    compile_id = await setup.store.create("acme", "alice", record)
    return record, compile_id, make_task(setup, compile_id=compile_id, record=record)


async def test_configuration_state_has_exact_fields_and_distinct_ids(setup):
    record = new_record(PARAMS)
    first = await setup.store.create("acme", "alice", record)
    second = await setup.store.create("acme", "alice", record)
    assert first != second
    assert set(await setup.store.get("acme", "alice", first)) == STATE_FIELDS
    assert record["task_first_create_time"] is record["last_run_time"] is None
    assert len(setup.files.files) == 2
    assert all(path.endswith("/state.json") for path in setup.files.files)
    assert await setup.store.get("another-account", "alice", first) is None
    assert not setup.fs.commits
    with pytest.raises(InvalidArgumentError):
        await setup.store.get("acme", "alice", "../escape")


async def test_acceptance_times_are_idempotent_and_never_move_backwards(setup):
    record = new_record(PARAMS)
    compile_id = await setup.store.create("acme", "alice", record)
    first = make_task(setup, compile_id=compile_id, record=record, created_at=100)
    later = make_task(
        setup, compile_id=compile_id, record=record, created_at=300, trigger="scheduled"
    )
    delayed = make_task(setup, compile_id=compile_id, record=record, created_at=200)
    await setup.lifecycle.record_queued(first)
    await asyncio.gather(
        setup.lifecycle.record_queued(later), setup.lifecycle.record_queued(delayed)
    )
    await setup.lifecycle.record_queued(first)
    current = await setup.store.get("acme", "alice", compile_id)
    assert current["task_first_create_time"] == 100
    assert current["last_run_time"] == 300
    runs = await setup.store.list_runs("acme", "alice", compile_id, limit=2)
    assert [run["created_at"] for run in runs] == [300, 200]
    assert (await setup.store.list_runs("acme", "alice", compile_id, cursor=runs[-1]["task_id"]))[
        0
    ]["created_at"] == 100
    assert len(await setup.store.list_runs("acme", "alice", compile_id)) == 3


async def test_full_execution_never_touches_compile_storage_or_source_snapshots(setup):
    class ForbiddenStore:
        def __getattr__(self, name):
            raise AssertionError(f"Full compile accesses configuration storage: {name}")

    lifecycle = CompileLifecycle(ForbiddenStore(), setup.fs)
    task = make_task(setup)
    assert await lifecycle.on_start(task) == "proceed"
    assert setup.fs.commits == [[TARGET]]
    plan = await lifecycle.runtime_changes(task.meta["request"], "acme", task.task_id, "alice")
    assert plan["execution"] == {"mode": "full"}
    assert plan["head"] is plan["base"] is None
    assert plan["full_dirs"] == [SOURCE] and plan["diff_dirs"] == []
    setup.fs.output_changes = [{"uri": TARGET + "/page.md", "change_type": "added"}]
    outcome = await lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert outcome.result == {"created": 1, "updated": 0, "deleted": 0}
    assert setup.fs.commits == [[TARGET], [TARGET]]
    assert not setup.files.files
    setup.finalize.assert_awaited_once()


async def test_registered_manual_and_scheduled_share_progress_and_compact_runs(setup):
    record = new_record(PARAMS)
    compile_id = await setup.store.create("acme", "alice", record)
    first = make_task(setup, compile_id=compile_id, record=record)
    await setup.lifecycle.record_queued(first)
    assert await setup.lifecycle.on_start(first) == "proceed"
    first_plan = await setup.lifecycle.runtime_changes(
        first.meta["request"], "acme", first.task_id, "alice"
    )
    assert first_plan["execution"]["mode"] == "full"
    setup.find.side_effect = [SimpleNamespace(total=7), SimpleNamespace(total=2)]
    await setup.lifecycle.on_finish(first, ExternalTaskSnapshot(status="completed"))
    state = await setup.store.get("acme", "alice", compile_id)
    assert state["progress"]["source_commit"] == first_plan["head"]
    second = make_task(
        setup, compile_id=compile_id, record=state, created_at=200, trigger="scheduled"
    )
    await setup.lifecycle.record_queued(second)
    outcome = await setup.lifecycle.on_start(second)
    assert isinstance(outcome, ExternalTaskSnapshot) and outcome.status == "completed"
    await setup.lifecycle.on_finish(second, outcome)
    run = await setup.store.get_run("acme", "alice", compile_id, second.task_id)
    await setup.lifecycle.on_finish(first, ExternalTaskSnapshot(status="completed"))
    assert (await setup.store.get("acme", "alice", compile_id))["conflict_num"] == 2
    assert setup.find.await_count == 2
    assert set(run) == {
        "task_id",
        "actor",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "params",
        "result",
        "error",
        "meta",
    }
    assert set(run["meta"]) == {
        "trigger",
        "mode",
        "base_snapshot",
        "start_snapshot",
        "finish_snapshot",
        "skill_snapshot_id",
    }
    assert run["meta"]["trigger"] == "scheduled"
    assert run["meta"]["base_snapshot"] == first_plan["head"]
    assert run["meta"]["finish_snapshot"] == run["meta"]["start_snapshot"]
    assert len(setup.fs.commits) == 3
    assert (await setup.store.get("acme", "alice", compile_id))["progress"]["source_commit"] == run[
        "meta"
    ]["start_snapshot"]


async def test_changes_new_roots_and_updated_configuration_do_not_rewrite_frozen_inputs(setup):
    record = new_record(PARAMS)
    record["progress"] = {
        "source_commit": "a" * 40,
        "processed_roots": [SOURCE],
        "instruction": None,
        "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
    }
    record["from"] = [SOURCE, "viking://resources/added"]
    compile_id = await setup.store.create("acme", "alice", record)
    task = make_task(setup, compile_id=compile_id, record=record)
    setup.fs.source_changes = [{"uri": SOURCE + "/gone.md", "change_type": "deleted"}]
    assert await setup.lifecycle.on_start(task) == "proceed"
    plan = await setup.lifecycle.runtime_changes(
        task.meta["request"], "acme", task.task_id, "alice"
    )
    assert plan["diff_dirs"] == [SOURCE]
    assert plan["full_dirs"] == ["viking://resources/added"]
    async with setup.store.edit("acme", "alice", compile_id) as current:
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
    assert (await setup.store.get("acme", "alice", compile_id))["progress"] == {}
    run = await setup.store.get_run("acme", "alice", compile_id, task.task_id)
    assert run["params"]["instruction"] is None
    assert (await setup.store.get("acme", "alice", compile_id))["conflict_num"] is None


async def test_failed_and_cancelled_runs_preserve_the_successful_baseline(setup):
    record = new_record(PARAMS)
    record["progress"] = {
        "source_commit": "a" * 40,
        "processed_roots": [SOURCE],
        "instruction": None,
        "skill_snapshot": {"uri": SKILL, "snapshot_id": None},
    }
    compile_id = await setup.store.create("acme", "alice", record)
    setup.fs.source_changes = [{"uri": SOURCE + "/a.md", "change_type": "modified"}]
    for status in ("failed", "cancelled"):
        task = make_task(setup, compile_id=compile_id, record=record)
        assert await setup.lifecycle.on_start(task) == "proceed"
        await setup.lifecycle.on_finish(
            task, ExternalTaskSnapshot(status=status, error_message="failure")
        )
        run = await setup.store.get_run("acme", "alice", compile_id, task.task_id)
        assert run["status"] == status
        assert run["meta"]["finish_snapshot"] is not None
        assert (await setup.store.get("acme", "alice", compile_id))["progress"] == record[
            "progress"
        ]


async def test_restart_preserves_target_claim_across_configuration_ids_and_full_runs(setup):
    first_id = await setup.store.create("acme", "alice", new_record(PARAMS))
    other_id = await setup.store.create("acme", "alice", new_record(PARAMS))
    first = make_task(setup, compile_id=first_id, record=new_record(PARAMS))
    other = make_task(setup, compile_id=other_id, record=new_record(PARAMS))
    transient = make_task(setup)
    assert await setup.lifecycle.on_start(first) == "proceed"
    replacement = CompileLifecycle(setup.store, setup.fs)
    assert await replacement.on_start(other) == "busy"
    assert await replacement.on_start(transient) == "busy"
    await replacement.on_finish(first, ExternalTaskSnapshot(status="completed"))
    assert await replacement.on_start(transient) == "proceed"
    assert await setup.lifecycle.on_start(other) == "busy"


async def test_duplicate_start_reuses_exact_snapshots_and_plan(setup, registered):
    record, compile_id, task = registered
    duplicate = deepcopy(task)
    another_worker = CompileLifecycle(setup.store, setup.fs)
    results = await asyncio.gather(
        setup.lifecycle.on_start(task), another_worker.on_start(duplicate)
    )
    assert results == ["proceed", "proceed"]
    assert setup.fs.commits == [[TARGET, SOURCE]]


async def test_completion_replay_cannot_overwrite_a_newer_baseline(setup, registered):
    record, compile_id, task = registered
    await setup.lifecycle.on_start(task)
    await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    async with setup.store.edit("acme", "alice", compile_id) as current:
        current["progress"]["source_commit"] = "f" * 40
    await CompileLifecycle(setup.store, setup.fs).on_finish(
        task, ExternalTaskSnapshot(status="completed")
    )
    assert (await setup.store.get("acme", "alice", compile_id))["progress"][
        "source_commit"
    ] == "f" * 40


@pytest.mark.parametrize("args", [{"mode": "full"}, {"last_compile_time": "now"}, {"changes": {}}])
def test_legacy_mode_and_forged_plan_controls_are_rejected(args):
    with pytest.raises(InvalidArgumentError):
        split_control_args(args)


async def test_durable_task_scan_ignores_a_process_local_stale_cache():
    files = _FakeAgfs()
    store = PersistentTaskStore(files)
    first = TaskRecord(
        task_id="cmp_" + "a" * 32, task_type="compile", account_id="acme", user_id="alice"
    )
    await store.create(first)
    await store.get_task(first.task_id, account_id="acme", user_id="alice")
    path = store._task_path("acme", "alice", first.task_id)
    payload = json.loads(files.files[path])
    payload["auth"] = {"compile_execution": {"phase": "committing"}}
    files.files[path] = json.dumps(payload).encode()
    current = (await store.list_durable("acme", task_type="compile"))[0]
    assert current.auth["compile_execution"]["phase"] == "committing"
    cached = await store.get_task(first.task_id, account_id="acme", user_id="alice")
    assert cached.auth == {}


async def test_committing_recovery_does_not_resubmit_or_replace_the_start_plan(setup, registered):
    record, compile_id, task = registered
    await setup.lifecycle.on_start(task)
    original_plan = deepcopy(setup.tracker.tasks[task.task_id].auth["compile_execution"]["plan"])
    setup.finalize.side_effect = ResourceBusyError("index is busy")
    outcome = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert outcome.status == "running" and outcome.stage == "committing"
    assert (await setup.store.get("acme", "alice", compile_id))["progress"] == {}
    restarted = CompileLifecycle(setup.store, setup.fs)
    resumed = deepcopy(task)
    resumed.auth = {}
    terminal = await restarted.on_start(resumed)
    assert isinstance(terminal, ExternalTaskSnapshot) and terminal.status == "completed"
    assert len(setup.fs.commits) == 1
    setup.finalize.side_effect = None
    assert (await restarted.on_finish(resumed, terminal)).status == "completed"
    assert setup.tracker.tasks[task.task_id].auth["compile_execution"]["plan"] == original_plan
    assert len(setup.fs.commits) == 2


async def test_cleanup_resumes_past_new_runs_and_retains_pending_recovery(setup):
    record = new_record(PARAMS)
    compile_id = await setup.store.create("acme", "alice", record)
    tasks = []
    for created in (100, 200, 300):
        task = make_task(setup, compile_id=compile_id, record=record, created_at=created)
        await setup.lifecycle.record_queued(task)
        run = await setup.store.get_run("acme", "alice", compile_id, task.task_id)
        run.update(status="completed", finished_at=created)
        await setup.store.put_run("acme", "alice", compile_id, run)
        setup.tracker.tasks[task.task_id].status = TaskStatus.COMPLETED
        tasks.append(task)
    # The oldest run still owns unsettled Work and must remain available for recovery.
    setup.tracker.tasks[tasks[0].task_id].status = TaskStatus.RUNNING
    now = 7 * 24 * 3600 + 250
    assert await setup.store.delete_expired(now=now, limit=1) == 0
    assert await setup.store.delete_expired(now=now, limit=1) == 1
    assert await setup.store.delete_expired(now=now, limit=1) == 0
    other_id = await setup.store.create("acme", "bob", record)
    old_run = dict(run, finished_at=100)
    await setup.store.put_run("acme", "bob", other_id, old_run)
    assert await setup.store.delete_expired(now=now, limit=10) == 0
    assert await setup.store.delete_expired(now=now, limit=10) == 1
    assert await setup.store.list_runs("acme", "bob", other_id) == []
    remaining = await setup.store.list_runs("acme", "alice", compile_id)
    assert [run["created_at"] for run in remaining] == [300, 100]
    assert (await setup.store.get("acme", "alice", compile_id))["last_run_time"] == 300


async def test_full_skill_inside_source_is_live_and_does_not_snapshot_the_source(setup):
    task = make_task(setup)
    task.meta["request"]["from"] = ["viking://agent/skills"]
    task.meta["request"]["skill"] = "viking://agent/skills/compiler"
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    assert await setup.lifecycle.on_start(task) == "proceed"
    assert setup.fs.commits == [[TARGET]]
    plan = await setup.lifecycle.runtime_changes(
        task.meta["request"], "acme", task.task_id, "alice"
    )
    assert plan["skill_snapshot"]["snapshot_id"] is None
    assert plan["head"] is None


async def test_cancel_during_index_finalization_does_not_publish_or_advance_progress(
    setup, registered
):
    record, compile_id, task = registered
    await setup.lifecycle.on_start(task)

    async def cancel_during_finalize(*args, **kwargs):
        setup.tracker.cancelled.add(task.task_id)

    setup.finalize.side_effect = cancel_during_finalize
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert result.status == "cancelled"
    assert (await setup.store.get("acme", "alice", compile_id))["progress"] == {}
    run = await setup.store.get_run("acme", "alice", compile_id, task.task_id)
    assert run["status"] == "cancelled"
    assert run["meta"]["finish_snapshot"] is not None
    assert run["result"] is None


async def test_schedule_registration_and_state_publication_share_the_configuration_lock(setup):
    compile_id = "cpt_" + "d" * 32
    registered = asyncio.Event()
    publish = asyncio.Event()

    async def register():
        registered.set()
        await publish.wait()

    async def callback():
        async with setup.store.edit("acme", "alice", compile_id) as record:
            return record["to"]

    creation = asyncio.create_task(
        setup.store.create(
            "acme", "alice", new_record(PARAMS), compile_id=compile_id, before_publish=register
        )
    )
    await registered.wait()
    delivery = asyncio.create_task(callback())
    await asyncio.sleep(0)
    assert not delivery.done()
    publish.set()
    assert await creation == compile_id
    assert await delivery == TARGET


async def test_registration_failure_does_not_publish_configuration(setup):
    compile_id = "cpt_" + "e" * 32
    with pytest.raises(RuntimeError, match="registration failed"):
        await setup.store.create(
            "acme",
            "alice",
            new_record(PARAMS),
            compile_id=compile_id,
            before_publish=AsyncMock(side_effect=RuntimeError("registration failed")),
        )
    assert await setup.store.get("acme", "alice", compile_id) is None
    assert not setup.files.files


async def test_runtime_error_is_redacted_before_run_persistence(setup, registered):
    record, compile_id, task = registered
    await setup.lifecycle.on_start(task)
    outcome = await setup.lifecycle.on_finish(
        task,
        ExternalTaskSnapshot(
            status="failed",
            error_message="Bad request sk-secret_value Authorization: Bearer bearer-secret",
        ),
    )
    run = await setup.store.get_run("acme", "alice", compile_id, task.task_id)
    assert "secret_value" not in run["error"]
    assert "bearer-secret" not in run["error"]
    assert "[REDACTED]" in run["error"]
    assert outcome.error_message == run["error"]
    assert "secret_value" not in json.dumps(
        await setup.store.list_runs("acme", "alice", compile_id)
    )


async def test_cancel_after_publication_still_restores_the_start_snapshot(setup, registered):
    record, compile_id, task = registered
    await setup.lifecycle.on_start(task)
    original_diff = setup.fs.diff_paths

    async def cancel_after_publication(*, dirs, **kwargs):
        if dirs == [TARGET] and len(setup.fs.commits) == 2:
            setup.tracker.cancelled.add(task.task_id)
        return await original_diff(dirs=dirs, **kwargs)

    setup.fs.diff_paths = cancel_after_publication
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert result.status == "cancelled"
    run = await setup.store.get_run("acme", "alice", compile_id, task.task_id)
    assert run["meta"]["finish_snapshot"] == f"{4:040x}"
    assert run["result"] is None
    assert (await setup.store.get("acme", "alice", compile_id))["progress"] == {}
    # A replay sees a completed rollback and cannot create further commits.
    await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="cancelled"))
    assert len(setup.fs.commits) == 4


async def test_first_acceptance_discovers_a_task_whose_state_publication_was_interrupted(setup):
    record = new_record(PARAMS)
    compile_id = await setup.store.create("acme", "alice", record)
    scheduled = make_task(
        setup, compile_id=compile_id, record=record, created_at=100, trigger="scheduled"
    )
    # The Task and run survive, but the configuration mutation does not publish.
    unpublished = deepcopy(record)
    await setup.lifecycle.record_queued(scheduled, locked_record=unpublished)
    assert (await setup.store.get("acme", "alice", compile_id))["task_first_create_time"] is None
    manual = make_task(setup, compile_id=compile_id, record=record, created_at=200)
    await setup.lifecycle.record_queued(manual)
    state = await setup.store.get("acme", "alice", compile_id)
    assert state["task_first_create_time"] == 100
    assert state["last_run_time"] == 200
    await setup.lifecycle.record_queued(scheduled)
    assert (await setup.store.get("acme", "alice", compile_id))["task_first_create_time"] == 100


async def test_creator_directories_support_direct_and_account_wide_paging(setup, monkeypatch):
    ids = ["cpt_" + digit * 32 for digit in "123"]
    for owner, compile_id in zip(("bob", "alice", "bob"), ids, strict=True):
        await setup.store.create("acme", owner, new_record(PARAMS), compile_id=compile_id)
    assert set(setup.files.files) == {
        f"/local/acme/_system/compile/tasks/{owner}/{compile_id}/state.json"
        for owner, compile_id in zip(("bob", "alice", "bob"), ids, strict=True)
    }
    assert await setup.store.get("acme", "bob", ids[1]) is None
    assert await setup.store.get("another-account", "alice", ids[1]) is None
    all_records = [record async for record in setup.store.iter_records("acme")]
    assert [compile_id for compile_id, _ in all_records] == ids
    assert [
        compile_id async for compile_id, _ in setup.store.iter_records("acme", cursor=ids[0])
    ] == ids[1:]
    listing = AsyncMock(wraps=setup.files.ls)
    monkeypatch.setattr(setup.files, "ls", listing)
    assert [
        compile_id async for compile_id, _ in setup.store.iter_records("acme", user_id="alice")
    ] == [ids[1]]
    listing.assert_awaited_once_with("/local/acme/_system/compile/tasks/alice")
    for owner in ("..", "../bob", "/bob", "", None):
        with pytest.raises(InvalidArgumentError):
            await setup.store.get("acme", owner, ids[0])
    with pytest.raises(InvalidArgumentError, match="owner cannot change"):
        async with setup.store.edit("acme", "alice", ids[1]) as record:
            record["execution_context"]["owner_user_id"] = "bob"


@pytest.mark.parametrize("total,expected", [(0, 0), (3, 3), (100, 100), (140, 100), (None, None)])
async def test_final_conflict_count_and_search_failure(setup, total, expected):
    record = new_record(PARAMS)
    record["conflict_num"] = 9
    compile_id = await setup.store.create("acme", "alice", record)
    task = make_task(setup, compile_id=compile_id, record=record, user_id="bob")
    setup.lifecycle.resolve_groups = AsyncMock(return_value=["reviewers"])

    async def find(**kwargs):
        setup.finalize.assert_awaited_once()
        assert len(setup.fs.commits) == 2
        if total is None:
            raise OSError("search unavailable")
        return SimpleNamespace(total=total)

    setup.find.side_effect = find
    await setup.lifecycle.on_start(task)
    assert (
        await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    ).status == "completed"
    assert (await setup.store.get("acme", "alice", compile_id))["conflict_num"] == expected
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
    record, compile_id, task = registered
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
    assert (await setup.store.get("acme", "alice", compile_id))["conflict_num"] is None
    assert setup.tracker.tasks[task.task_id].auth["compile_execution"]["phase"] != "finished"
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    setup.find.side_effect = AssertionError("recovery must reuse the persisted statistic")
    result = await CompileLifecycle(setup.store, setup.fs).on_finish(
        resumed, ExternalTaskSnapshot(status="completed")
    )
    assert result.status == "completed"
    assert (await setup.store.get("acme", "alice", compile_id))["conflict_num"] == 9
    assert setup.find.await_count == 1


@pytest.mark.parametrize("reindex_status", [TaskStatus.COMPLETED, TaskStatus.FAILED, None])
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_rollback_conflict_count_waits_for_tracked_reindex(
    setup, registered, reindex_status, status
):
    record, compile_id, task = registered
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
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status=status))
    assert result.status == status
    state = await setup.store.get("acme", "alice", compile_id)
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
    compile_id = await setup.store.create("acme", "alice", record)
    running = make_task(setup, compile_id=compile_id, record=record)
    queued = make_task(setup, compile_id=compile_id, record=record, created_at=200)
    await setup.lifecycle.on_start(running)
    assert await setup.lifecycle.on_start(queued) == "busy"
    result = await setup.lifecycle.on_finish(queued, ExternalTaskSnapshot(status="cancelled"))
    assert result.status == "cancelled"
    assert (await setup.store.get("acme", "alice", compile_id))["conflict_num"] is None
    setup.find.assert_not_awaited()
