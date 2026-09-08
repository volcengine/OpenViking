# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from openviking.session.compressor_v3 import _case_experience_links_via_trajectories
from openviking.session.memory import experience_case_links as case_links
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.experience_case_links import (
    acquire_case_link_lease,
    release_case_link_lease,
    sync_experience_case_links,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.components.experience_feedback import record_experience_feedback_stats
from openviking.session.train.domain import Trajectory
from openviking.storage.errors import LockAcquisitionError

ROOT = "viking://user/u/memories"
EXP = f"{ROOT}/experiences/rule.md"
CASE = f"{ROOT}/cases/first.md"
CASE2 = f"{ROOT}/cases/second.md"
TRAJ = f"{ROOT}/trajectories/first.md"
TRAJ2 = f"{ROOT}/trajectories/second.md"


def link(source, target, kind="related_to"):
    return StoredLink(from_uri=source, to_uri=target, link_type=kind, weight=1.0).model_dump()


def memory(uri, status=None, links=(), backlinks=()):
    kind = uri.split("/memories/")[1].split("/")[0]
    fields = {"memory_type": kind, "case_name": "test", "version": 1}
    if status:
        fields["status"] = status
    return MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            memory_type=kind,
            content="body",
            extra_fields=fields,
            links=list(links),
            backlinks=list(backlinks),
        )
    )


class FakeFS:
    def __init__(self, status="draft", old_link=False):
        self._async_agfs = self
        self.files = {
            EXP: memory(
                EXP,
                status,
                [link(EXP, TRAJ, "derived_from")],
                [link(CASE, EXP)] if old_link else [],
            ),
            TRAJ: memory(TRAJ, backlinks=[link(CASE, TRAJ, "partial_trajectory")]),
            CASE: memory(
                CASE,
                links=[
                    link(CASE, TRAJ, "partial_trajectory"),
                    *([link(CASE, EXP)] if old_link else []),
                ],
            ),
        }
        self.writes = []
        self.acquired = []
        self.released = []
        self.lock_timeouts = []
        self.on_acquire = None
        self.fail_write = None

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        assert uri in lease_ref
        if uri == self.fail_write:
            raise OSError("write failed")
        self.files[uri] = content
        self.writes.append(uri)

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
        self.acquired.append(paths)
        self.lock_timeouts.append(timeout_secs)
        if self.on_acquire:
            callback, self.on_acquire = self.on_acquire, None
            callback()
        return paths

    async def pathlock_release(self, lease):
        self.released.append(lease)

    def parsed(self, uri):
        return MemoryFileUtils.read(self.files[uri], uri=uri)

    def status(self, status):
        exp = self.parsed(EXP)
        exp.extra_fields["status"] = status
        self.files[EXP] = MemoryFileUtils.write(exp)


class ContendedFS(FakeFS):
    """Model AGFS's bounded wait without serializing non-overlapping paths."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.held = set()
        self.condition = asyncio.Condition()
        self.waiting = asyncio.Event()

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
        async with self.condition:
            if self.held.intersection(paths):
                self.waiting.set()
                if timeout_secs <= 0:
                    raise LockAcquisitionError("lock acquire timed out after 0ms")
                try:
                    await asyncio.wait_for(
                        self.condition.wait_for(lambda: not self.held.intersection(paths)),
                        timeout=timeout_secs,
                    )
                except TimeoutError as exc:
                    raise LockAcquisitionError("lock acquire timed out") from exc
            self.held.update(paths)
            return await super().pathlock_acquire_exact_batch(paths, timeout_secs)

    async def pathlock_release(self, lease):
        async with self.condition:
            self.held.difference_update(lease)
            self.condition.notify_all()
            await super().pathlock_release(lease)


def negative_trajectories():
    return [
        Trajectory(
            name=name,
            uri=uri,
            content="execution",
            outcome="failure",
            retrieval_anchor="",
            metadata={"experience_effects": {"negative_ids": ["E1"]}},
        )
        for name, uri in [("first", TRAJ), ("second", TRAJ2)]
    ]


async def test_link_lease_uses_short_native_attempt_and_deduplicated_paths():
    fs = FakeFS()
    lease = await acquire_case_link_lease(fs, [EXP, CASE, EXP])
    assert lease == sorted([EXP, CASE])
    assert fs.lock_timeouts == [case_links._LOCK_ATTEMPT_SECONDS]
    await fs.pathlock_release(lease)


async def test_link_lease_retries_conflicts_within_one_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(case_links, "monotonic", lambda: now[0])
    timeouts = []

    class BusyClient:
        async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
            timeouts.append(timeout_secs)
            now[0] += 0.04
            raise LockAcquisitionError("lock acquire timed out")

    with pytest.raises(TimeoutError, match="wait budget exhausted") as caught:
        await acquire_case_link_lease(BusyClient(), [EXP], deadline=100.1)
    assert len(timeouts) == 3
    assert timeouts == pytest.approx([0.05, 0.05, 0.02])
    assert isinstance(caught.value.__cause__, LockAcquisitionError)


@pytest.mark.parametrize(
    "error", [OSError("disk failed"), ValueError("invalid path"), TimeoutError("I/O timeout")]
)
async def test_link_lease_does_not_retry_non_contention_errors(error):
    calls = []

    class FailedClient:
        async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
            calls.append(paths)
            raise error

    with pytest.raises(type(error), match=str(error)):
        await acquire_case_link_lease(FailedClient(), [EXP])
    assert calls == [[EXP]]


async def test_cancel_during_backoff_stops_without_another_attempt(monkeypatch):
    monkeypatch.setattr(case_links, "_LOCK_RETRY_INITIAL_SECONDS", 1.0)
    attempted = asyncio.Event()
    calls = []

    class BusyClient:
        async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
            calls.append(paths)
            attempted.set()
            raise LockAcquisitionError("lock acquire timed out")

    task = asyncio.create_task(acquire_case_link_lease(BusyClient(), [EXP]))
    await asyncio.wait_for(attempted.wait(), timeout=1)
    await asyncio.sleep(0.01)  # First attempt has completed; caller is in async backoff.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)
    assert calls == [[EXP]]


def test_thirty_native_waiters_do_not_starve_file_io_or_release(tmp_path, monkeypatch):
    from openviking.pyagfs import AsyncAGFSClient, RAGFSBindingClient

    if RAGFSBindingClient is None:
        pytest.skip("Native AGFS binding is not available")
    monkeypatch.setattr(case_links, "CASE_LINK_LOCK_TIMEOUT_SECONDS", 5.0)

    async def exercise():
        # More waiting commits than workers must leave the holder able to do I/O
        # and release; no reliance on the machine's default thread-pool size.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=4))
        client = RAGFSBindingClient()
        client.mount("localfs", "/local", {"local_dir": str(tmp_path)})
        locks = AsyncAGFSClient(client)
        path = "/local/default/experiences/rule.md"
        held = await locks.pathlock_acquire_exact_batch([path])

        async def waiter():
            lease = await acquire_case_link_lease(locks, [path])
            try:
                # Holder work uses the very same shared worker pool.
                await locks.ls("/local")
            finally:
                await locks.pathlock_release(lease)

        tasks = [asyncio.create_task(waiter()) for _ in range(30)]
        try:
            await asyncio.sleep(0.03)
            await asyncio.wait_for(locks.ls("/local"), timeout=1.5)
            await asyncio.wait_for(locks.pathlock_release(held), timeout=1.5)
            held = None
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if held is not None:
                await locks.pathlock_release(held)
        next_lease = await locks.pathlock_acquire_exact_batch([path])
        await locks.pathlock_release(next_lease)

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_link_lease_retries_real_agfs_contention(tmp_path, monkeypatch, cancel_waiter):
    from openviking.pyagfs import AsyncAGFSClient, RAGFSBindingClient

    if RAGFSBindingClient is None:
        pytest.skip("Native AGFS binding is not available")
    monkeypatch.setattr(case_links, "CASE_LINK_LOCK_TIMEOUT_SECONDS", 2.0)
    client = RAGFSBindingClient()
    client.mount("localfs", "/local", {"local_dir": str(tmp_path)})
    locks = AsyncAGFSClient(client)
    path = "/local/default/experiences/rule.md"
    held = await locks.pathlock_acquire_exact_batch([path])
    pending = asyncio.create_task(acquire_case_link_lease(locks, [path]))
    try:
        await asyncio.sleep(0.03)
        assert not pending.done()
        if cancel_waiter:
            pending.cancel()
            await asyncio.sleep(0)
    finally:
        await locks.pathlock_release(held)
        if cancel_waiter:
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            acquired = await pending
            await locks.pathlock_release(acquired)

    # No leaked owner prevents the next writer from acquiring immediately.
    next_lease = await locks.pathlock_acquire_exact_batch([path])
    await locks.pathlock_release(next_lease)


@pytest.mark.parametrize("acquisition_fails", [False, True])
async def test_cancelled_acquisition_drains_and_releases_late_lease(acquisition_fails):
    entered = asyncio.Event()
    finish = asyncio.Event()
    lease = {"lease_ref": "late-lease"}
    released = []

    class DelayedLockClient:
        async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
            assert timeout_secs > 0
            entered.set()
            await finish.wait()
            if acquisition_fails:
                raise TimeoutError("lock wait expired")
            return lease

        async def pathlock_release(self, acquired):
            released.append(acquired)

    task = asyncio.create_task(acquire_case_link_lease(DelayedLockClient(), [EXP]))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # Repeated cancellation must not cancel native acquisition.
    await asyncio.sleep(0)
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert released == ([] if acquisition_fails else [lease])


@pytest.mark.parametrize("operation", ["sync", "feedback"])
async def test_repeated_cancellation_during_owned_lease_release_does_not_leak(operation):
    class DelayedFS(ContendedFS):
        def __init__(self):
            super().__init__("promoted", old_link=True)
            self.body_entered = asyncio.Event()
            self.release_entered = asyncio.Event()
            self.allow_release = asyncio.Event()

        async def write_file(self, uri, content, ctx=None, lease_ref=None):
            self.body_entered.set()
            await asyncio.Event().wait()

        async def pathlock_release(self, lease):
            self.release_entered.set()
            await self.allow_release.wait()
            await super().pathlock_release(lease)

    fs = DelayedFS()
    work = (
        sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
        if operation == "sync"
        else record_experience_feedback_stats(
            trajectories=negative_trajectories(),
            injected_reminders=[{"id": "E1", "experience_uri": EXP}],
            viking_fs=fs,
            ctx=None,
        )
    )
    task = asyncio.create_task(work)
    try:
        await asyncio.wait_for(fs.body_entered.wait(), timeout=1)
        task.cancel()
        await asyncio.wait_for(fs.release_entered.wait(), timeout=1)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
    finally:
        fs.allow_release.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    assert not fs.held
    assert fs.acquired == fs.released


def test_cancelled_release_queued_behind_native_worker_still_releases(tmp_path):
    from openviking.pyagfs import AsyncAGFSClient, RAGFSBindingClient

    if RAGFSBindingClient is None:
        pytest.skip("Native AGFS binding is not available")

    async def exercise():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        binding = RAGFSBindingClient()
        binding.mount("localfs", "/local", {"local_dir": str(tmp_path)})
        client = AsyncAGFSClient(binding)
        path = "/local/default/rule.md"
        lease = await client.pathlock_acquire_exact_batch([path])
        gate = threading.Event()
        worker_entered = threading.Event()
        release_entered = asyncio.Event()

        def occupy_worker():
            worker_entered.set()
            gate.wait(timeout=3)

        class ReleaseClient:
            async def pathlock_release(self, owned):
                release_entered.set()
                await client.pathlock_release(owned)

        blocker = asyncio.create_task(asyncio.to_thread(occupy_worker))
        while not worker_entered.is_set():
            await asyncio.sleep(0.001)
        release = asyncio.create_task(release_case_link_lease(ReleaseClient(), lease))
        try:
            await asyncio.wait_for(release_entered.wait(), timeout=1)
            release.cancel()
            await asyncio.sleep(0)
            release.cancel()
            await asyncio.sleep(0)
            assert not release.done()
        finally:
            gate.set()
            await blocker
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(release, timeout=1)
        # The queued native release was not discarded by cancellation.
        next_lease = await client.pathlock_acquire_exact_batch([path])
        await client.pathlock_release(next_lease)

    asyncio.run(exercise())


async def test_sync_waits_for_overlapping_lock_then_rechecks_status():
    fs = ContendedFS("promoted")
    held = await fs.pathlock_acquire_exact_batch([EXP])
    task = asyncio.create_task(sync_experience_case_links(EXP, viking_fs=fs, ctx=None))
    try:
        await asyncio.wait_for(fs.waiting.wait(), timeout=1)
        assert not task.done()
        fs.status("draft")  # Concurrent writer wins while synchronization waits.
    finally:
        await fs.pathlock_release(held)
        await asyncio.wait_for(task, timeout=1)
    assert EXP not in [x["to_uri"] for x in fs.parsed(CASE).links]
    assert not fs.parsed(EXP).backlinks
    assert not fs.held


async def test_waiting_on_one_experience_does_not_block_other_experience():
    fs = ContendedFS("promoted")
    exp2 = f"{ROOT}/experiences/other.md"
    fs.files[exp2] = memory(exp2, "promoted", [link(exp2, TRAJ2, "derived_from")])
    fs.files[TRAJ2] = memory(TRAJ2, backlinks=[link(CASE2, TRAJ2)])
    fs.files[CASE2] = memory(CASE2)
    held = await fs.pathlock_acquire_exact_batch([EXP])
    task = asyncio.create_task(sync_experience_case_links(EXP, viking_fs=fs, ctx=None))
    try:
        await asyncio.wait_for(fs.waiting.wait(), timeout=1)
        await asyncio.wait_for(sync_experience_case_links(exp2, viking_fs=fs, ctx=None), timeout=1)
        assert not task.done()
        assert exp2 in [x["to_uri"] for x in fs.parsed(CASE2).links]
    finally:
        await fs.pathlock_release(held)
        await asyncio.wait_for(task, timeout=1)
    assert not fs.held


async def test_concurrent_experiences_preserve_each_others_links_on_shared_case():
    class YieldingFS(ContendedFS):
        async def read_file(self, uri, ctx=None):
            await asyncio.sleep(0)
            return await super().read_file(uri, ctx)

        async def write_file(self, uri, content, ctx=None, lease_ref=None):
            await asyncio.sleep(0)
            await super().write_file(uri, content, ctx, lease_ref)

    fs = YieldingFS("promoted")
    exp2 = f"{ROOT}/experiences/other.md"
    fs.files[exp2] = memory(exp2, "promoted", [link(exp2, TRAJ, "derived_from")])
    await asyncio.wait_for(
        asyncio.gather(
            sync_experience_case_links(EXP, viking_fs=fs, ctx=None),
            sync_experience_case_links(exp2, viking_fs=fs, ctx=None),
        ),
        timeout=2,
    )
    assert {x["to_uri"] for x in fs.parsed(CASE).links} == {TRAJ, EXP, exp2}
    for exp_uri in (EXP, exp2):
        assert [x["from_uri"] for x in fs.parsed(exp_uri).backlinks] == [CASE]
    fs.status("degraded")
    await asyncio.wait_for(
        asyncio.gather(
            sync_experience_case_links(EXP, viking_fs=fs, ctx=None),
            sync_experience_case_links(exp2, viking_fs=fs, ctx=None),
        ),
        timeout=2,
    )
    assert {x["to_uri"] for x in fs.parsed(CASE).links} == {TRAJ, exp2}
    assert not fs.parsed(EXP).backlinks
    assert [x["from_uri"] for x in fs.parsed(exp2).backlinks] == [CASE]
    assert not fs.held
    assert fs.acquired == fs.released


async def test_feedback_waits_for_conflict_without_losing_or_replaying_observations():
    fs = ContendedFS("promoted", old_link=True)
    held = await fs.pathlock_acquire_exact_batch([EXP])
    task = asyncio.create_task(
        record_experience_feedback_stats(
            trajectories=negative_trajectories(),
            injected_reminders=[{"id": "E1", "experience_uri": EXP}],
            viking_fs=fs,
            ctx=None,
        )
    )
    try:
        await asyncio.wait_for(fs.waiting.wait(), timeout=1)
        assert not task.done()
        assert not fs.writes
    finally:
        await fs.pathlock_release(held)
        result = await asyncio.wait_for(task, timeout=1)
    assert not result.errors
    fields = fs.parsed(EXP).extra_fields
    assert fields["status"] == "degraded"
    assert fields["feedback_stats"]["injected_count"] == 2
    assert fields["feedback_stats"]["negative_count"] == 2
    assert EXP not in [x["to_uri"] for x in fs.parsed(CASE).links]
    assert not fs.held


async def test_lock_timeout_is_bounded_and_does_not_write_feedback_or_links(monkeypatch):
    monkeypatch.setattr(case_links, "CASE_LINK_LOCK_TIMEOUT_SECONDS", 0.01)
    fs = ContendedFS("promoted", old_link=True)
    held = await fs.pathlock_acquire_exact_batch([EXP])
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                sync_experience_case_links(EXP, viking_fs=fs, ctx=None), timeout=1
            )
        result = await asyncio.wait_for(
            record_experience_feedback_stats(
                trajectories=negative_trajectories(),
                injected_reminders=[{"id": "E1", "experience_uri": EXP}],
                viking_fs=fs,
                ctx=None,
            ),
            timeout=1,
        )
        assert result.errors
        assert not result.updated_uris
        assert not fs.writes
        assert fs.parsed(EXP).extra_fields["status"] == "promoted"
        assert "feedback_stats" not in fs.parsed(EXP).extra_fields
    finally:
        await fs.pathlock_release(held)
    assert not fs.held


@pytest.mark.parametrize(
    "status,expected",
    [
        ("draft", 0),
        ("degraded", 0),
        ("archived", 0),
        ("promoted", 1),
        (None, 0),
        ("unknown", 0),
        ("production", 1),
    ],
)
def test_plan_links_only_promoted(status, expected):
    plan = SimpleNamespace(
        items=[
            SimpleNamespace(
                kind="upsert",
                memory_type="experiences",
                target_uri=EXP,
                links=[link(EXP, TRAJ, "derived_from")],
            )
        ]
    )
    applied = SimpleNamespace(
        written_uris=[EXP],
        edited_uris=[],
        updated_policy_set=SimpleNamespace(
            root_uri=f"{ROOT}/experiences",
            policies=[SimpleNamespace(uri=EXP, status=status)],
        ),
    )
    links = _case_experience_links_via_trajectories(
        case_uri=CASE, trajectory_uris={TRAJ}, plan=plan, apply_result=applied
    )
    assert len(links) == expected


async def test_draft_promotion_backfills_earlier_cases_and_degradation_removes_links():
    fs = FakeFS()
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.parsed(EXP).backlinks
    assert EXP not in [x["to_uri"] for x in fs.parsed(CASE).links]
    assert fs.parsed(EXP).links == [link(EXP, TRAJ, "derived_from")]

    fs.files[CASE2] = memory(CASE2, links=[link(CASE2, TRAJ2)])
    fs.files[TRAJ2] = memory(TRAJ2, backlinks=[link(CASE2, TRAJ2)])
    exp = fs.parsed(EXP)
    exp.links.append(link(EXP, TRAJ2, "derived_from"))
    exp.extra_fields["status"] = "promoted"
    fs.files[EXP] = MemoryFileUtils.write(exp)
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert {x["from_uri"] for x in fs.parsed(EXP).backlinks} == {CASE, CASE2}
    for case in (CASE, CASE2):
        assert EXP in [x["to_uri"] for x in fs.parsed(case).links]
        assert "../experiences/rule.md" in fs.files[case]
    writes = list(fs.writes)
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert fs.writes == writes

    fs.status("degraded")
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.parsed(EXP).backlinks
    for case in (CASE, CASE2):
        assert EXP not in [x["to_uri"] for x in fs.parsed(case).links]
        assert "../experiences/rule.md" not in fs.files[case]
        assert len(fs.parsed(case).links) == 1  # trajectory provenance retained
    assert fs.acquired == fs.released


@pytest.mark.parametrize("status", ["draft", "degraded", "archived", None, "unknown"])
async def test_cleanup_existing_non_promoted_links(status):
    fs = FakeFS(status, old_link=True)
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.parsed(EXP).backlinks
    assert [x["to_uri"] for x in fs.parsed(CASE).links] == [TRAJ]


async def test_status_is_rechecked_under_lock():
    fs = FakeFS("promoted")
    fs.on_acquire = lambda: fs.status("draft")
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.parsed(EXP).backlinks
    assert EXP not in [x["to_uri"] for x in fs.parsed(CASE).links]


async def test_new_source_expands_lock_coverage(monkeypatch):
    fs = FakeFS("promoted")
    now = [100.0]
    monkeypatch.setattr(case_links, "monotonic", lambda: now[0])

    def add_source():
        now[0] += 40.0
        fs.files[TRAJ2] = memory(TRAJ2, backlinks=[link(CASE2, TRAJ2)])
        fs.files[CASE2] = memory(CASE2)
        exp = fs.parsed(EXP)
        exp.links.append(link(EXP, TRAJ2, "derived_from"))
        fs.files[EXP] = MemoryFileUtils.write(exp)

    fs.on_acquire = add_source
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert len(fs.acquired) == 2
    assert fs.lock_timeouts == [case_links._LOCK_ATTEMPT_SECONDS] * 2
    assert {CASE2, TRAJ2} <= set(fs.acquired[-1])
    assert EXP in [x["to_uri"] for x in fs.parsed(CASE2).links]
    assert fs.acquired == fs.released


async def test_expanding_locks_does_not_restart_exhausted_wait_budget(monkeypatch):
    fs = FakeFS("promoted")
    now = [100.0]
    monkeypatch.setattr(case_links, "monotonic", lambda: now[0])

    def add_source():
        now[0] += 300.0
        fs.files[TRAJ2] = memory(TRAJ2, backlinks=[link(CASE2, TRAJ2)])
        fs.files[CASE2] = memory(CASE2)
        exp = fs.parsed(EXP)
        exp.links.append(link(EXP, TRAJ2, "derived_from"))
        fs.files[EXP] = MemoryFileUtils.write(exp)

    fs.on_acquire = add_source
    with pytest.raises(TimeoutError, match="wait budget exhausted"):
        await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert len(fs.acquired) == 1
    assert fs.acquired == fs.released
    assert not fs.writes


async def test_failed_backlink_write_does_not_expose_forward_link():
    fs = FakeFS("promoted")
    fs.fail_write = EXP
    with pytest.raises(OSError, match="write failed"):
        await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.writes
    assert fs.acquired == fs.released


async def test_failed_cleanup_retains_backlink_for_retry():
    fs = FakeFS("degraded", old_link=True)
    fs.fail_write = CASE
    with pytest.raises(OSError, match="write failed"):
        await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert fs.parsed(EXP).backlinks
    fs.fail_write = None
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.parsed(EXP).backlinks


async def test_negative_feedback_degradation_cleans_case_links():
    fs = FakeFS("promoted", old_link=True)
    result = await record_experience_feedback_stats(
        trajectories=[
            Trajectory(
                name=name,
                uri=uri,
                content="execution",
                outcome="failure",
                retrieval_anchor="",
                metadata={"experience_effects": {"negative_ids": ["E1"]}},
            )
            for name, uri in [("first", TRAJ), ("second", TRAJ2)]
        ],
        injected_reminders=[{"id": "E1", "experience_uri": EXP}],
        viking_fs=fs,
        ctx=None,
    )
    assert result.errors == []
    assert fs.parsed(EXP).extra_fields["status"] == "degraded"
    assert not fs.parsed(EXP).backlinks
    assert [x["to_uri"] for x in fs.parsed(CASE).links] == [TRAJ]
    # The status write and link cleanup use separate, non-nested leases.
    assert fs.acquired[0] == [EXP]
    assert fs.acquired == fs.released


async def test_missing_source_does_not_remove_known_promoted_case_link():
    fs = FakeFS("promoted", old_link=True)
    del fs.files[TRAJ]
    await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert EXP in [x["to_uri"] for x in fs.parsed(CASE).links]
    assert [x["from_uri"] for x in fs.parsed(EXP).backlinks] == [CASE]


async def test_source_read_error_fails_without_publishing_links():
    fs = FakeFS("promoted")
    original_read = fs.read_file

    async def failing_read(uri, ctx=None):
        if uri == TRAJ:
            raise OSError("source read unavailable")
        return await original_read(uri, ctx)

    fs.read_file = failing_read
    with pytest.raises(OSError, match="source read unavailable"):
        await sync_experience_case_links(EXP, viking_fs=fs, ctx=None)
    assert not fs.writes
