"""Publication fault tests; fake backend models coordinated complete objects."""

import asyncio
import json
import threading
from collections import defaultdict
from types import SimpleNamespace

import pytest

from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    RelationSource,
    RelationSources,
    can_use_incremental_sources,
)
from openviking.service.artifact_relations_store import (
    ArtifactRelationsStore,
    ArtifactRelationsStoreError,
)
from openviking.service.compile_targets import CompileStateStore, new_record

ACCOUNT = "acme"
TO = "viking://resources/wiki"
PAGE = TO + "/a.md"


class Backend:
    def __init__(self):
        self.files = {}
        self.locks = defaultdict(asyncio.Lock)
        self.leases = {}
        self.fault = None
        self.reads = []
        self.cleanup_fails = False
        self.delay = None

    async def pathlock_acquire_batch_async(self, ctx, requests, timeout_secs, owner_lease_ref):
        directory = requests[-1]["path"]
        lock = self.locks[directory]
        await lock.acquire()
        ref = str(len(self.leases))
        self.leases[ref] = (lock, directory, True)
        return {"lease_ref": ref}

    def pathlock_release(self, ctx, lease):
        lock, directory, valid = self.leases[lease["lease_ref"]]
        self.leases[lease["lease_ref"]] = (lock, directory, False)
        if valid:
            lock.release()

    def _validate(self, path, ctx):
        _, directory, valid = self.leases[ctx["lease_ref"]]
        if not valid or not path.startswith(directory + "/"):
            raise RuntimeError("lease invalid")

    def read(self, path, **kwargs):
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def ensure_parent_dirs(self, path, **kwargs):
        pass

    def write(self, path, data, *, ctx):
        self._validate(path, ctx)
        self.files[path] = data

    def conditional_write(self, path, expected, data, *, ctx):
        self._validate(path, ctx)
        self._delay_io(path, data)
        if self.files.get(path) != expected:
            return False
        fault = self.fault
        selected = (
            fault
            and path.endswith(fault[0] + ".json")
            and (fault[2] is None or json.loads(data).get("status") == fault[2])
        )
        if selected:
            self.fault = None
            if fault[1] == "before":
                raise OSError("replacement failed")
        self.files[path] = data
        if selected and fault[1] == "after":
            raise OSError("response lost")
        return True

    def _delay_io(self, path, data):
        if self.delay and self.delay[0](path, json.loads(data)):
            _, loop, started, resume = self.delay
            self.delay = None
            loop.call_soon_threadsafe(started.set)
            assert resume.wait(10), "Timed out releasing delayed I/O"

    def replace(self, src, dst, *, ctx):
        self._validate(src, ctx)
        self._validate(dst, ctx)
        data = self.files[src]
        value = json.loads(data)
        self._delay_io(dst, data)
        fault = self.fault
        selected = fault and dst.endswith(fault[0] + ".json")
        if selected and (fault[2] is None or value.get("status") == fault[2]):
            self.fault = None
            if fault[1] == "before":
                raise OSError("replacement failed")
        self.files[dst] = data
        self.files.pop(src, None)
        if selected and fault[1] == "after":
            raise OSError("response lost")

    def rm(self, path, *, ctx, **kwargs):
        self._validate(path, ctx)
        if self.cleanup_fails:
            raise OSError("cleanup failed")
        if kwargs.get("recursive"):
            for key in list(self.files):
                if key.startswith(path + "/"):
                    del self.files[key]
        else:
            self.files.pop(path, None)


@pytest.fixture
def backend():
    backend = Backend()
    store = CompileStateStore(backend)
    backend.files[store._record_path(ACCOUNT, TO)] = json.dumps(new_record(TO)).encode()
    return backend


def stores(backend):
    return [ArtifactRelationsStore(CompileStateStore(backend)) for _ in range(2)]


def directory(store, backend):
    target = json.loads(backend.files[CompileStateStore(backend)._record_path(ACCOUNT, TO)])
    return store._directory(ACCOUNT, TO) + "/" + target["generation"]


def published_directory(store, backend):
    root = directory(store, backend)
    state = json.loads(backend.files[root + "/artifact_relations_state.json"])
    return root + "/builds/" + state["published_build_id"]


def compile_target(backend):
    path = CompileStateStore(backend)._record_path(ACCOUNT, TO)
    target = json.loads(backend.files[path])
    target.update(active_operation_id="op", active_task_id="task")
    backend.files[path] = json.dumps(target).encode()


def candidate(claimed, link="b.md"):
    sources = RelationSources(
        parser_version="markdown-v1",
        build_id=claimed.build_id,
        pages={PAGE: RelationSource(blob_oid="blob", links=[link])},
    )
    graph = ArtifactRelations(
        build_id=claimed.build_id,
        target_snapshot_id=claimed.target_snapshot_id,
        generated_at=1,
        nodes=[TO + "/", PAGE],
        edges=[],
    )
    return sources, graph


async def publish(store, snapshot="A"):
    await store.request(ACCOUNT, TO, snapshot)
    claimed = await store.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    await store.publish(ACCOUNT, TO, claimed, sources, graph)
    return claimed, graph


@pytest.mark.asyncio
async def test_missing_publication_and_empty_links(backend):
    store, _ = stores(backend)
    assert await store.get_baseline(ACCOUNT, TO) == (None, None, None)
    target = new_record(TO)
    target.update(active_operation_id="op", active_task_id="task")
    backend.files[CompileStateStore(backend)._record_path(ACCOUNT, TO)] = json.dumps(
        target
    ).encode()
    await store.merge_sources(
        ACCOUNT,
        TO,
        {PAGE: RelationSource(blob_oid="oid", links=[])},
        parser_version="v1",
        compile_operation_id="op",
    )
    _, sources, _ = await store.get_baseline(ACCOUNT, TO)
    assert sources.pages[PAGE].links == []
    assert PAGE + ".missing" not in sources.pages
    assert sources.build_id is None


@pytest.mark.asyncio
async def test_cross_instance_merge_preserves_both_batches(backend):
    one, two = stores(backend)
    target = new_record(TO)
    target.update(active_operation_id="op", active_task_id="task")
    backend.files[CompileStateStore(backend)._record_path(ACCOUNT, TO)] = json.dumps(
        target
    ).encode()
    await asyncio.gather(
        *[
            store.merge_sources(
                ACCOUNT,
                TO,
                {PAGE + str(i): RelationSource(blob_oid=str(i), links=[])},
                parser_version="v1",
                compile_operation_id="op",
            )
            for i, store in enumerate((one, two))
        ]
    )
    _, sources, _ = await two.get_baseline(ACCOUNT, TO)
    assert set(sources.pages) == {PAGE + "0", PAGE + "1"}
    assert sources.compile_operation_id == "op"


@pytest.mark.asyncio
async def test_new_compile_operation_replaces_staged_sources(backend):
    store, _ = stores(backend)
    compile_target(backend)
    await store.merge_sources(
        ACCOUNT,
        TO,
        {PAGE: RelationSource(blob_oid="old", links=[])},
        parser_version="v1",
        compile_operation_id="op",
    )
    path = CompileStateStore(backend)._record_path(ACCOUNT, TO)
    target = json.loads(backend.files[path])
    target["active_operation_id"] = "next"
    backend.files[path] = json.dumps(target).encode()

    await store.merge_sources(
        ACCOUNT,
        TO,
        {PAGE + ".new": RelationSource(blob_oid="new", links=[])},
        parser_version="v1",
        compile_operation_id="next",
    )

    _, sources, _ = await store.get_baseline(ACCOUNT, TO)
    assert set(sources.pages) == {PAGE + ".new"}
    assert sources.compile_operation_id == "next"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step", ["artifact_relations_state", "relation_sources", "artifact_relations"]
)
async def test_failure_before_publication_keeps_old_graph(backend, step):
    one, two = stores(backend)
    _, old = await publish(one)
    await one.request(ACCOUNT, TO, "B")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed, "new.md")
    backend.fault = (step, "before", None)
    with pytest.raises(OSError):
        await one.publish(ACCOUNT, TO, claimed, sources, graph)
    state, stored_sources, stored_graph = await two.get_baseline(ACCOUNT, TO)
    assert state.status == "building"
    assert stored_graph == old
    # Unpublished candidates cannot pollute the published source baseline.
    assert can_use_incremental_sources(stored_sources, old, parser_version="markdown-v1")
    assert (await one.fail(ACCOUNT, TO, claimed, "exhausted")).status == "failed"
    assert (await two.get_publication(ACCOUNT, TO))[1] == old
    assert not any(path.endswith(".tmp") for path in backend.files)


@pytest.mark.asyncio
async def test_ready_failure_recovers_from_graph_without_sources_read(backend):
    one, two = stores(backend)
    await publish(one)
    await one.request(ACCOUNT, TO, "B")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    backend.fault = ("artifact_relations_state", "before", "ready")
    with pytest.raises(OSError):
        await one.publish(ACCOUNT, TO, claimed, sources, graph)
    backend.reads.clear()
    state, stored_graph = await two.get_publication(ACCOUNT, TO)
    assert state.status == "ready"
    assert stored_graph == graph
    assert not any(path.endswith("relation_sources.json") for path in backend.reads)
    assert (await one.fail(ACCOUNT, TO, claimed, "wrong failure")).status == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step", ["artifact_relations_state", "relation_sources", "artifact_relations"]
)
async def test_lost_replace_response_is_confirmed(backend, step):
    one, two = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    backend.fault = (step, "after", None)
    assert (await one.publish(ACCOUNT, TO, claimed, sources, graph)).status == "ready"
    assert (await two.get_publication(ACCOUNT, TO))[1] == graph


@pytest.mark.asyncio
async def test_recovery_write_failure_is_not_fabricated_ready(backend):
    one, two = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    backend.fault = ("artifact_relations_state", "before", "ready")
    with pytest.raises(OSError):
        await one.publish(ACCOUNT, TO, claimed, sources, graph)
    backend.fault = ("artifact_relations_state", "before", "ready")
    with pytest.raises(OSError):
        await two.get_publication(ACCOUNT, TO)
    assert (await two.get_publication(ACCOUNT, TO))[0].status == "ready"


@pytest.mark.asyncio
async def test_pending_does_not_change_claimed_snapshot(backend):
    one, two = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    claimed = await one.claim(ACCOUNT, TO)
    state = await two.request(ACCOUNT, TO, "B")
    assert state.pending and state.target_snapshot_id == "A"
    sources, graph = candidate(claimed)
    assert (await one.publish(ACCOUNT, TO, claimed, sources, graph)).pending


@pytest.mark.asyncio
async def test_claim_does_not_load_graph_or_sources(backend):
    store, _ = stores(backend)
    await publish(store)
    await store.request(ACCOUNT, TO, "B")
    backend.reads.clear()

    claimed = await store.claim(ACCOUNT, TO)

    assert claimed.phase == "computing"
    assert not any(
        path.endswith(("/artifact_relations.json", "/relation_sources.json"))
        for path in backend.reads
    )


@pytest.mark.asyncio
async def test_new_compile_rejects_old_worker(backend):
    one, two = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    target = new_record(TO)
    target.update(active_operation_id="op", active_task_id="task")
    backend.files[CompileStateStore(backend)._record_path(ACCOUNT, TO)] = json.dumps(
        target
    ).encode()
    await two.request(ACCOUNT, TO, "B", compile_operation_id="op")
    with pytest.raises(ArtifactRelationsStoreError, match="ownership"):
        await one.publish(ACCOUNT, TO, claimed, sources, graph)
    assert (await two.get_publication(ACCOUNT, TO))[1] is None


@pytest.mark.asyncio
async def test_reclaimed_request_rejects_stale_build_id(backend):
    one, two = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    await one.fail(ACCOUNT, TO, claimed, "failed")
    await two.request(ACCOUNT, TO, "A")
    await two.claim(ACCOUNT, TO)
    with pytest.raises(ArtifactRelationsStoreError, match="Build ownership"):
        await one.publish(ACCOUNT, TO, claimed, sources, graph)


@pytest.mark.asyncio
async def test_cleanup_error_does_not_hide_published_graph(backend):
    one, _ = stores(backend)
    backend.cleanup_fails = True
    _, graph = await publish(one)
    assert (await one.get_publication(ACCOUNT, TO))[1] == graph


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["cleaned", "cleanup-fails"])
async def test_compile_publish_cleans_only_its_staged_sources(backend, cleanup_fails):
    store, _ = stores(backend)
    compile_target(backend)
    await store.merge_sources(
        ACCOUNT,
        TO,
        {PAGE: RelationSource(blob_oid="blob", links=[])},
        parser_version="markdown-v1",
        compile_operation_id="op",
    )
    await store.request(ACCOUNT, TO, "A", compile_operation_id="op")
    claimed = await store.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    backend.cleanup_fails = cleanup_fails

    state = await store.publish(ACCOUNT, TO, claimed, sources, graph)

    staging = directory(store, backend) + "/relation_sources.json"
    assert state.status == "ready"
    assert (await store.get_publication(ACCOUNT, TO))[1] == graph
    assert (staging in backend.files) is cleanup_fails

    if cleanup_fails:
        backend.cleanup_fails = False
        await store.publish(ACCOUNT, TO, claimed, sources, graph)
        assert staging not in backend.files


@pytest.mark.asyncio
async def test_ready_identity_corruption_is_rejected(backend):
    one, _ = stores(backend)
    await publish(one)
    path = published_directory(one, backend) + "/artifact_relations.json"
    raw = json.loads(backend.files[path])
    raw["build_id"] = "different"
    backend.files[path] = json.dumps(raw).encode()
    with pytest.raises(ArtifactRelationsStoreError, match="does not match"):
        await one.get_publication(ACCOUNT, TO)


@pytest.mark.asyncio
async def test_invalid_candidate_does_not_touch_storage(backend):
    one, _ = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    sources.build_id = "wrong"
    before = dict(backend.files)
    with pytest.raises(ArtifactRelationsStoreError, match="identities"):
        await one.publish(ACCOUNT, TO, claimed, sources, graph)
    assert backend.files == before


@pytest.mark.parametrize("account", ["", "..", "a/b", r"a\b"])
def test_unsafe_account_rejected(backend, account):
    one, _ = stores(backend)
    with pytest.raises(ValueError):
        one._directory(account, TO)


def test_target_path_normalization(backend):
    one, _ = stores(backend)
    assert one._directory(ACCOUNT, TO + " space/") == one._directory(ACCOUNT, TO + "_space")


@pytest.mark.asyncio
async def test_retry_keeps_version_pending_and_attempt_count(backend):
    one, two = stores(backend)
    await one.request(ACCOUNT, TO, "A")
    first = await one.claim(ACCOUNT, TO)
    await two.request(ACCOUNT, TO, "B")
    state = await one.fail(ACCOUNT, TO, first, "temporary", retry=True)
    assert state.phase == "queued" and state.pending and state.attempts == 1
    second = await two.claim(ACCOUNT, TO)
    assert second.target_snapshot_id == "A"
    assert second.attempts == 2 and second.build_id != first.build_id
    sources, graph = candidate(first)
    with pytest.raises(ArtifactRelationsStoreError, match="Build ownership"):
        await one.publish(ACCOUNT, TO, first, sources, graph)


@pytest.mark.asyncio
async def test_repeated_compile_poll_does_not_reset_queued_retry_attempts(backend):
    one, two = stores(backend)
    path = CompileStateStore(backend)._record_path(ACCOUNT, TO)
    target = json.loads(backend.files[path])
    target.update(active_operation_id="op", active_task_id="task")
    backend.files[path] = json.dumps(target).encode()
    await one.request(ACCOUNT, TO, "A", compile_operation_id="op")
    for attempt in range(1, 4):
        claimed = await one.claim(ACCOUNT, TO)
        assert claimed.attempts == attempt
        await one.fail(ACCOUNT, TO, claimed, "failure", retry=attempt < 3)
        # Compile finish polling and recovery may both repeat the same request.
        state = await two.request(ACCOUNT, TO, "A", compile_operation_id="op")
        assert state.attempts == attempt
    assert state.status == "failed"


@pytest.mark.asyncio
async def test_repeated_request_transition_does_not_rewrite_state(backend):
    store, _ = stores(backend)
    state, notify = await store.request_transition(ACCOUNT, TO, "A")
    path = directory(store, backend) + "/artifact_relations_state.json"
    first_bytes = backend.files[path]

    repeated, notify_again = await store.request_transition(ACCOUNT, TO, "A")

    assert notify and not notify_again
    assert repeated.revision == state.revision
    assert backend.files[path] == first_bytes


@pytest.mark.asyncio
async def test_compile_snapshot_freezes_batch_merges(backend):
    one, _ = stores(backend)
    target = new_record(TO)
    target.update(active_operation_id="op", active_task_id="task")
    backend.files[CompileStateStore(backend)._record_path(ACCOUNT, TO)] = json.dumps(
        target
    ).encode()
    await one.request(ACCOUNT, TO, "A", compile_operation_id="op")
    with pytest.raises(ArtifactRelationsStoreError, match="already registered"):
        await one.merge_sources(ACCOUNT, TO, {}, parser_version="v1", compile_operation_id="op")
    with pytest.raises(ArtifactRelationsStoreError, match="snapshot cannot change"):
        await one.request(ACCOUNT, TO, "B", compile_operation_id="op")


@pytest.mark.asyncio
async def test_query_does_not_depend_on_corrupted_sources(backend):
    one, _ = stores(backend)
    _, graph = await publish(one)
    path = published_directory(one, backend) + "/relation_sources.json"
    backend.files[path] = b"invalid-json"
    assert (await one.get_publication(ACCOUNT, TO))[1] == graph
    assert (await one.get_baseline(ACCOUNT, TO))[1] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["artifact_relations", "ready"])
async def test_late_io_after_lease_loss_cannot_replace_new_publication(backend, step):
    one, two = stores(backend)
    await publish(one)
    await one.request(ACCOUNT, TO, "B")
    claimed = await one.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    started, resume = asyncio.Event(), threading.Event()
    backend.delay = (
        lambda path, value: (
            path.endswith("artifact_relations.json")
            if step == "artifact_relations"
            else path.endswith("artifact_relations_state.json") and value["status"] == "ready"
        ),
        asyncio.get_running_loop(),
        started,
        resume,
    )
    running = asyncio.create_task(one.publish(ACCOUNT, TO, claimed, sources, graph))
    await asyncio.wait_for(started.wait(), 5)
    # Storage request already passed lease validation. Expire its ownership
    # while it is in flight and let another instance publish a later build.
    ref = next(ref for ref, (_, _, valid) in backend.leases.items() if valid)
    backend.pathlock_release({}, {"lease_ref": ref})
    state, _ = await two.get_publication(ACCOUNT, TO)
    if state.status == "building":
        await two.fail(ACCOUNT, TO, claimed, "expired")
    _, latest = await publish(two, "C")
    resume.set()
    with pytest.raises((ArtifactRelationsStoreError, RuntimeError)):
        await running
    assert (await two.get_publication(ACCOUNT, TO))[1] == latest


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed_build", [False, True], ids=["queued", "computing"])
async def test_compile_timeout_survives_restart_and_rejects_late_worker(
    backend, monkeypatch, claimed_build
):
    clock = SimpleNamespace(time=lambda: 100.0)
    monkeypatch.setattr("openviking.service.artifact_relations_store.time", clock)
    one, _ = stores(backend)
    _, old_graph = await publish(one)
    compile_target(backend)
    await one.request(ACCOUNT, TO, "B", compile_operation_id="op")
    claimed = await one.claim(ACCOUNT, TO) if claimed_build else None
    clock.time = lambda: 399.0
    # A fresh instance must use the original durable request time.
    two = ArtifactRelationsStore(CompileStateStore(backend))
    await two.request(ACCOUNT, TO, "B", compile_operation_id="op")
    state, _ = await two.check_compile_wait(ACCOUNT, TO, "B", "op", 300)
    assert state.status == "building" and state.requested_at == 100
    clock.time = lambda: 400.0
    state, graph = await two.check_compile_wait(ACCOUNT, TO, "B", "op", 300)
    assert state.status == "failed" and state.error_code == "ARTIFACT_RELATIONS_TIMEOUT"
    assert graph is None
    assert (await one.request(ACCOUNT, TO, "B", compile_operation_id="op")).status == "failed"
    if claimed:
        sources, graph = candidate(claimed)
        with pytest.raises(ArtifactRelationsStoreError, match="Build ownership"):
            await one.publish(ACCOUNT, TO, claimed, sources, graph)


@pytest.mark.asyncio
async def test_compile_retry_does_not_refresh_timeout_clock(backend, monkeypatch):
    clock = SimpleNamespace(time=lambda: 100.0)
    monkeypatch.setattr("openviking.service.artifact_relations_store.time", clock)
    store, _ = stores(backend)
    compile_target(backend)
    await store.request(ACCOUNT, TO, "A", compile_operation_id="op")
    first = await store.claim(ACCOUNT, TO)
    clock.time = lambda: 200.0
    await store.fail(ACCOUNT, TO, first, "retry", retry=True)
    await store.request(ACCOUNT, TO, "A", compile_operation_id="op")
    second = await store.claim(ACCOUNT, TO)
    assert second.requested_at == 100 and second.attempts == 2
    clock.time = lambda: 400.0
    state, _ = await store.check_compile_wait(ACCOUNT, TO, "A", "op", 300)
    assert state.error_code == "ARTIFACT_RELATIONS_TIMEOUT"


@pytest.mark.asyncio
async def test_building_compile_poll_does_not_load_published_graph(backend):
    store, _ = stores(backend)
    await publish(store)
    compile_target(backend)
    await store.request(ACCOUNT, TO, "B", compile_operation_id="op")
    backend.reads.clear()

    await store.request(ACCOUNT, TO, "B", compile_operation_id="op")
    state, graph = await store.check_compile_wait(ACCOUNT, TO, "B", "op", 300)

    assert state.status == "building" and graph is None
    assert not any(path.endswith("/artifact_relations.json") for path in backend.reads)


@pytest.mark.asyncio
async def test_compile_baseline_does_not_load_unused_published_graph(backend):
    store, _ = stores(backend)
    await publish(store)
    compile_target(backend)
    await store.merge_sources(
        ACCOUNT,
        TO,
        {PAGE: RelationSource(blob_oid="new", links=[])},
        parser_version="markdown-v1",
        compile_operation_id="op",
    )
    await store.request(ACCOUNT, TO, "B", compile_operation_id="op")
    backend.reads.clear()

    state, sources, graph = await store.get_baseline(ACCOUNT, TO)

    assert state.target_snapshot_id == "B"
    assert sources.compile_operation_id == "op"
    assert graph is None
    assert not any(path.endswith("/artifact_relations.json") for path in backend.reads)


@pytest.mark.asyncio
async def test_completed_graph_wins_expiry_and_mismatched_request_is_untouched(
    backend, monkeypatch
):
    clock = SimpleNamespace(time=lambda: 100.0)
    monkeypatch.setattr("openviking.service.artifact_relations_store.time", clock)
    store, _ = stores(backend)
    compile_target(backend)
    await store.request(ACCOUNT, TO, "A", compile_operation_id="op")
    claimed = await store.claim(ACCOUNT, TO)
    clock.time = lambda: 500.0
    for snapshot, operation in [("other", "op"), ("A", "other")]:
        state, _ = await store.check_compile_wait(ACCOUNT, TO, snapshot, operation, 300)
        assert state.status == "building"
    sources, graph = candidate(claimed)
    await store.publish(ACCOUNT, TO, claimed, sources, graph)
    state, result = await store.check_compile_wait(ACCOUNT, TO, "A", "op", 300)
    assert state.status == "ready" and result == graph


@pytest.mark.asyncio
async def test_legacy_compile_request_initializes_timeout_once(backend, monkeypatch):
    clock = SimpleNamespace(time=lambda: 100.0)
    monkeypatch.setattr("openviking.service.artifact_relations_store.time", clock)
    store, _ = stores(backend)
    compile_target(backend)
    await store.request(ACCOUNT, TO, "A", compile_operation_id="op")
    path = directory(store, backend) + "/artifact_relations_state.json"
    raw = json.loads(backend.files[path])
    del raw["requested_at"]
    backend.files[path] = json.dumps(raw).encode()
    clock.time = lambda: 500.0
    state, _ = await store.check_compile_wait(ACCOUNT, TO, "A", "op", 300)
    assert state.requested_at == 500
    other = ArtifactRelationsStore(CompileStateStore(backend))
    clock.time = lambda: 800.0
    state, _ = await other.check_compile_wait(ACCOUNT, TO, "A", "op", 300)
    assert state.status == "failed" and state.requested_at == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "queued", "computing", "failed"])
async def test_status_read_does_not_load_graph_or_sources(backend, phase):
    store, _ = stores(backend)
    await publish(store)
    if phase != "ready":
        await store.request(ACCOUNT, TO, "B")
        if phase != "queued":
            claimed = await store.claim(ACCOUNT, TO)
            if phase == "failed":
                await store.fail(ACCOUNT, TO, claimed, "failed")
    # A damaged old payload must not be downloaded during obligation scanning.
    graph_path = published_directory(store, backend) + "/artifact_relations.json"
    backend.files[graph_path] = b"invalid JSON"
    backend.reads.clear()
    state = await store.get_state(ACCOUNT, TO)
    assert state.status == (phase if phase in {"ready", "failed"} else "building")
    assert sum(path.endswith("/artifact_relations_state.json") for path in backend.reads) == 1
    assert not any(
        path.endswith(("/artifact_relations.json", "/relation_sources.json"))
        for path in backend.reads
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True], ids=["missing-candidate", "complete-candidate"])
async def test_status_recovery_reads_only_publishing_candidate(backend, complete):
    store, _ = stores(backend)
    await publish(store)
    await store.request(ACCOUNT, TO, "B")
    claimed = await store.claim(ACCOUNT, TO)
    sources, graph = candidate(claimed)
    backend.fault = (
        ("artifact_relations_state", "before", "ready")
        if complete
        else ("artifact_relations", "before", None)
    )
    with pytest.raises(OSError):
        await store.publish(ACCOUNT, TO, claimed, sources, graph)
    backend.reads.clear()
    state = await store.get_state(ACCOUNT, TO)
    assert state.status == ("ready" if complete else "building")
    candidate_path = (
        directory(store, backend) + f"/builds/{claimed.build_id}/artifact_relations.json"
    )
    assert [path for path in backend.reads if path.endswith("/artifact_relations.json")] == [
        candidate_path
    ]
    assert not any(path.endswith("/relation_sources.json") for path in backend.reads)
    if complete:
        assert (await store.get_publication(ACCOUNT, TO))[1] == graph
