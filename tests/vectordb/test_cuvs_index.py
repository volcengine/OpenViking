# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from openviking.storage.vectordb.index.cuvs_index import (
    CuVSDenseIndex,
    CuVSMemoryBudgetError,
    CuVSNativeRouteError,
    CuVSSearchTelemetry,
    CuVSUnavailableError,
    UnsupportedCuVSFilterError,
    _CuVSRuntime,
    estimate_cuvs_memory,
    matches_filter,
)
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord


class FakeCuVSRuntime:
    """CPU implementation of the tiny runtime boundary used by unit tests."""

    def __init__(self, metric="inner_product"):
        self.metric = metric
        self.dataset = []
        self.build_count = 0
        self.prepare_filter_count = 0
        self.closed = False
        self.free_memory_bytes = 1 << 60
        self.total_memory_bytes = 1 << 60
        self.release_count = 0

    def build(self, dataset):
        self.dataset = [list(vector) for vector in dataset]
        self.build_count += 1
        return self.dataset

    def search(self, index, query, limit, mask):
        rows = []
        for offset, vector in enumerate(index):
            if mask is not None and not mask[offset]:
                continue
            if self.metric == "sqeuclidean":
                distance = sum(
                    (left - right) ** 2 for left, right in zip(query, vector, strict=True)
                )
                sort_key = distance
            else:
                distance = sum(left * right for left, right in zip(query, vector, strict=True))
                sort_key = -distance
            rows.append((sort_key, offset, distance))
        rows.sort()
        selected = rows[:limit]
        return [row[1] for row in selected], [row[2] for row in selected]

    def prepare_filter(self, mask):
        self.prepare_filter_count += 1
        return tuple(mask)

    def memory_info(self):
        return self.free_memory_bytes, self.total_memory_bytes

    def release_index(self):
        self.dataset = []
        self.release_count += 1

    @staticmethod
    def is_out_of_memory(_exc):
        return False

    def close(self):
        self.closed = True


def test_cuvs_runtime_uses_captured_device_from_worker_thread():
    """Every CUDA call must select the device captured by the runtime."""

    class DeviceSpy:
        def __init__(self, expected_device):
            self.expected_device = expected_device
            self.local = threading.local()
            self.lock = threading.Lock()
            self.operations = []
            self.releases = []
            self.temporary_releases = []
            self.track_temporaries = False
            self.temporary_count = 0
            self.resource_count = 0
            self.fail_build = False
            self.fail_search = False

        def set_current(self, device_id):
            self.local.current = device_id

        def current(self):
            return getattr(self.local, "current", None)

        def require_captured_device(self, operation):
            assert self.current() == self.expected_device, operation
            with self.lock:
                self.operations.append(operation)

        def device(self, device_id):
            spy = self

            class DeviceContext:
                def __enter__(self):
                    self.previous = spy.current()
                    spy.set_current(device_id)
                    return self

                def __exit__(self, _exc_type, _exc, _traceback):
                    spy.set_current(self.previous)

            return DeviceContext()

    spy = DeviceSpy(expected_device=3)

    class FakeArray:
        def __init__(self, values, dtype, release_name=None):
            self.values = values
            self.dtype = dtype
            self.release_name = release_name

        def __getitem__(self, offset):
            value = self.values[offset]

            class HostRow(list):
                def tolist(self):
                    return list(self)

            return HostRow(value) if isinstance(value, list) else value

        def __del__(self):
            if self.release_name is not None:
                spy.temporary_releases.append((self.release_name, spy.current()))

    class FakeRuntimeAPI:
        @staticmethod
        def memGetInfo():
            spy.require_captured_device("memory_info")
            return 100, 200

    class FakeMemory:
        class OutOfMemoryError(Exception):
            pass

    class FakeCuda:
        runtime = FakeRuntimeAPI()
        memory = FakeMemory()

        @staticmethod
        def Device(device_id):
            return spy.device(device_id)

    class FakeMemoryPool:
        @staticmethod
        def free_all_blocks():
            spy.require_captured_device("release_index")

    class FakeCuPy:
        float16 = "float16"
        float32 = "float32"
        uint32 = "uint32"
        ndarray = FakeArray
        cuda = FakeCuda()

        @staticmethod
        def asarray(values, dtype):
            spy.require_captured_device("asarray")
            release_name = None
            if spy.track_temporaries:
                spy.temporary_count += 1
                release_name = f"array-{spy.temporary_count}"
            return FakeArray(values, dtype, release_name)

        @staticmethod
        def asnumpy(values):
            spy.require_captured_device("asnumpy")
            return values

        @staticmethod
        def get_default_memory_pool():
            spy.require_captured_device("memory_pool")
            return FakeMemoryPool()

    class FakeResources:
        def __init__(self):
            spy.require_captured_device("resources_create")
            spy.resource_count += 1
            self.name = f"resources-{spy.resource_count}"
            self.device_id = spy.current()

        def sync(self):
            spy.require_captured_device("resources_sync")
            assert spy.current() == self.device_id

        def __del__(self):
            spy.releases.append((self.name, spy.current()))

    class FakeBruteForce:
        @staticmethod
        def build(_dataset, metric):
            spy.require_captured_device("build")
            assert metric == "inner_product"
            if spy.fail_build:
                raise RuntimeError("injected build failure")
            return object()

        @staticmethod
        def search(_index, _queries, limit, prefilter, resources):
            spy.require_captured_device("search")
            assert limit == 1
            assert prefilter is not None
            assert resources.device_id == spy.expected_device
            if spy.fail_search:
                raise RuntimeError("injected search failure")
            return FakeArray([[0.25]], "float32", "distances"), FakeArray(
                [[0]], "int64", "neighbors"
            )

    class FakeFilters:
        @staticmethod
        def from_bitset(bitset):
            spy.require_captured_device("filter")
            return bitset

    runtime = _CuVSRuntime.__new__(_CuVSRuntime)
    runtime.cp = FakeCuPy()
    runtime.brute_force = FakeBruteForce()
    runtime.cagra = object()
    runtime.filters = FakeFilters()
    runtime.Resources = FakeResources
    runtime.device_id = spy.expected_device
    runtime.dtype = "float32"
    runtime.device_dtype = FakeCuPy.float32
    runtime.algorithm = "brute_force"
    runtime.metric = "inner_product"
    runtime.build_params = {}
    runtime.search_params = {}
    runtime._resource_condition = threading.Condition(threading.Lock())
    runtime._available_resources = []
    runtime._owned_resources = []
    runtime._resource_limit = 1
    runtime._resources_closed = False
    runtime._use_explicit_resources = False
    runtime.set_max_concurrent_searches(2)
    constructing_thread = threading.get_ident()

    def use_runtime_from_worker():
        assert threading.get_ident() != constructing_thread
        spy.set_current(9)

        assert runtime.memory_info() == (100, 200)
        assert spy.current() == 9
        runtime_index = runtime.build([[1.0, 0.0]])
        assert spy.current() == 9
        runtime.prepare_filter([True])
        runtime.prepare_filter_words([1])
        runtime._prefilter([True])
        assert spy.current() == 9
        spy.track_temporaries = True
        assert runtime.search(runtime_index, [1.0, 0.0], 1, [True]) == ([0], [0.25])
        spy.track_temporaries = False
        assert {name for name, _device in spy.temporary_releases} == {
            "array-1",
            "array-2",
            "distances",
            "neighbors",
        }
        assert all(device == 3 for _name, device in spy.temporary_releases)
        assert spy.current() == 9
        spy.fail_build = True
        spy.track_temporaries = True
        with pytest.raises(RuntimeError, match="injected build failure"):
            runtime.build([[0.0, 1.0]])
        spy.fail_build = False
        spy.track_temporaries = False
        assert ("array-3", 3) in spy.temporary_releases
        assert spy.current() == 9
        spy.fail_search = True
        spy.track_temporaries = True
        with pytest.raises(RuntimeError, match="injected search failure"):
            runtime.search(runtime_index, [0.0, 1.0], 1, [True])
        spy.fail_search = False
        spy.track_temporaries = False
        assert ("array-4", 3) in spy.temporary_releases
        assert ("array-5", 3) in spy.temporary_releases
        assert spy.current() == 9
        assert spy.releases == [("resources-1", 3)]
        assert runtime.search(runtime_index, [1.0, 0.0], 1, [True]) == ([0], [0.25])
        runtime.release_index()
        assert spy.current() == 9
        return runtime_index

    with ThreadPoolExecutor(max_workers=1) as executor:
        runtime_index = executor.submit(use_runtime_from_worker).result(timeout=5)

    churn_errors = []

    def search_from_short_lived_thread():
        try:
            spy.set_current(9)
            assert runtime.search(runtime_index, [1.0, 0.0], 1, [True]) == ([0], [0.25])
            assert spy.current() == 9
        except BaseException as exc:
            churn_errors.append(exc)

    for _ in range(8):
        thread = threading.Thread(target=search_from_short_lived_thread)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert churn_errors == []
    assert spy.resource_count == 2
    assert len(runtime._owned_resources) <= 2
    assert len(runtime._available_resources) == 1

    # A failed search discarded the first resource under device 3. The bounded
    # pool reused the replacement across short-lived threads and retains it
    # until close can also destroy it on the captured device.
    assert spy.releases == [("resources-1", 3)]
    spy.set_current(9)
    runtime.close()
    assert spy.current() == 9
    assert spy.releases == [("resources-1", 3), ("resources-2", 3)]

    assert {
        "memory_info",
        "memory_pool",
        "release_index",
        "asarray",
        "build",
        "resources_create",
        "resources_sync",
        "filter",
        "search",
        "asnumpy",
    } <= set(spy.operations)


def candidate(label, vector, **fields):
    import json

    return CandidateData(label=label, vector=vector, fields=json.dumps(fields))


def delta(label, vector, **fields):
    import json

    return DeltaRecord(label=label, vector=vector, fields=json.dumps(fields))


def test_cuvs_rebuild_objects_are_released_on_the_captured_device():
    class DeviceOwned:
        def __init__(self, runtime, name):
            self.runtime = runtime
            self.name = name

        def __del__(self):
            self.runtime.released.append((self.name, self.runtime.current_device))

    class LifecycleRuntime:
        def __init__(self):
            self.current_device = 9
            self.build_count = 0
            self.released = []
            self.closed = False

        def device_scope(self):
            runtime = self

            class DeviceContext:
                def __enter__(self):
                    self.previous = runtime.current_device
                    runtime.current_device = 3

                def __exit__(self, _exc_type, _exc, _traceback):
                    runtime.current_device = self.previous

            return DeviceContext()

        def build(self, _dataset):
            self.build_count += 1
            return DeviceOwned(self, f"build-{self.build_count}")

        def close(self):
            assert self.current_device == 3
            self.closed = True

    runtime = LifecycleRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])

    first = index.prepare_rebuild()
    assert first is not None and index.commit_rebuild(first)

    # Preparing a replacement drops the unused snapshot in the device scope.
    index.upsert([delta(2, [0.0, 1.0])])
    stale = index.prepare_rebuild()
    assert stale is not None
    assert ("build-1", 3) in runtime.released

    # A mutation makes the candidate stale; rejecting it releases its GPU data
    # on the captured device rather than whichever device this thread inherited.
    index.upsert([delta(3, [1.0, 1.0])])
    assert index.commit_rebuild(stale) is False
    assert ("build-2", 3) in runtime.released

    abandoned = index.prepare_rebuild()
    assert abandoned is not None
    index.discard_rebuild(abandoned)
    assert ("build-3", 3) in runtime.released

    final = index.prepare_rebuild()
    assert final is not None and index.commit_rebuild(final)
    index.close()
    assert runtime.closed is True
    assert ("build-4", 3) in runtime.released
    assert runtime.current_device == 9


def test_inflight_snapshot_is_released_on_captured_device_after_replacement():
    class DeviceOwned:
        def __init__(self, runtime, name):
            self.runtime = runtime
            self.name = name

        def __del__(self):
            self.runtime.released.append((self.name, self.runtime.current_device()))

    class ConcurrentLifecycleRuntime:
        def __init__(self):
            self.local = threading.local()
            self.build_count = 0
            self.released = []
            self.search_started = threading.Event()
            self.resume_search = threading.Event()

        def set_current_device(self, device_id):
            self.local.current_device = device_id

        def current_device(self):
            return getattr(self.local, "current_device", None)

        def device_scope(self):
            runtime = self

            class DeviceContext:
                def __enter__(self):
                    self.previous = runtime.current_device()
                    runtime.set_current_device(3)

                def __exit__(self, _exc_type, _exc, _traceback):
                    runtime.set_current_device(self.previous)

            return DeviceContext()

        def build(self, _dataset):
            self.build_count += 1
            return DeviceOwned(self, f"build-{self.build_count}")

        def search(self, _index, _query, _limit, _mask):
            self.search_started.set()
            assert self.resume_search.wait(timeout=5)
            return [0], [1.0]

        def close(self):
            assert self.current_device() == 3

    runtime = ConcurrentLifecycleRuntime()
    runtime.set_current_device(9)
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={"max_concurrent_gpu_searches": 2},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])
    first = index.prepare_rebuild()
    assert first is not None and index.commit_rebuild(first)

    def search_from_other_device():
        runtime.set_current_device(9)
        result = index.search([1.0, 0.0], 1, None)
        assert runtime.current_device() == 9
        return result

    with ThreadPoolExecutor(max_workers=1) as executor:
        inflight = executor.submit(search_from_other_device)
        assert runtime.search_started.wait(timeout=5)
        index.upsert([delta(2, [0.0, 1.0])])
        replacement = index.prepare_rebuild()
        assert replacement is not None and index.commit_rebuild(replacement)
        assert runtime.released == []
        runtime.resume_search.set()
        assert inflight.result(timeout=5)[0] == [1]

    assert ("build-1", 3) in runtime.released
    index.close()
    assert ("build-2", 3) in runtime.released
    assert runtime.current_device() == 9


def test_cuvs_rebuild_candidate_cannot_be_committed_twice():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])
    rebuild = index.prepare_rebuild()
    assert rebuild is not None and index.commit_rebuild(rebuild)

    with pytest.raises(RuntimeError, match="already been consumed"):
        index.commit_rebuild(rebuild)

    assert index.needs_rebuild is False
    assert index.search([1.0, 0.0], 1, None)[0] == [1]


def test_cuvs_registrar_failure_consumes_candidate_without_marking_clean():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])
    rebuild = index.prepare_rebuild()
    assert rebuild is not None

    def fail_registration(_labels):
        raise RuntimeError("injected registrar failure")

    with pytest.raises(RuntimeError, match="injected registrar failure"):
        index.commit_rebuild(rebuild, fail_registration)
    assert index.needs_rebuild is True
    with pytest.raises(RuntimeError, match="already been consumed"):
        index.commit_rebuild(rebuild)

    replacement = index.prepare_rebuild()
    assert replacement is not None and index.commit_rebuild(replacement)
    assert index.search([1.0, 0.0], 1, None)[0] == [1]


def test_cuvs_dense_search_handles_filter_upsert_delete_and_lazy_rebuild():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=True,
        field_types={"account_id": "string", "uri": "path"},
        config={"algorithm": "brute_force"},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a", uri="/docs/one"),
            candidate(20, [0.8, 0.2], account_id="a", uri="/docs/deep/two"),
            candidate(30, [0.0, 1.0], account_id="b", uri="/other/three"),
        ]
    )

    labels, scores = index.search(
        [10.0, 0.0],
        10,
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "account_id", "conds": ["a"]},
                {"op": "must", "field": "uri", "conds": ["/docs"], "para": "-d=1"},
            ],
        },
    )
    assert labels == [10]
    assert scores == [1.0]
    assert runtime.build_count == 1

    # Repeated reads reuse the GPU index; a mutation invalidates it exactly once.
    assert index.search([1.0, 0.0], 1, None)[0] == [10]
    assert runtime.build_count == 1
    index.upsert([delta(30, [2.0, 0.0], account_id="a", uri="/docs/three")])
    assert index.search([1.0, 0.0], 3, None)[0] == [10, 30, 20]
    assert runtime.build_count == 2
    index.delete([DeltaRecord(label=10)])
    assert index.search([1.0, 0.0], 3, None)[0] == [30, 20]
    assert runtime.build_count == 3


def test_empty_filter_does_not_allocate_a_device_bitset():
    class SearchCountingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.search_count = 0

        def search(self, index, query, limit, mask):
            self.search_count += 1
            return super().search(index, query, limit, mask)

    runtime = SearchCountingRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"tenant": "string"},
        config={"filter_cache_size": 0},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], tenant="a")])

    assert index.search(
        [1.0, 0.0],
        1,
        {"op": "must", "field": "tenant", "conds": ["missing"]},
    ) == ([], [])
    assert runtime.prepare_filter_count == 0
    assert runtime.search_count == 0


def test_cuvs_search_telemetry_records_build_filter_cache_and_search():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"algorithm": "brute_force"},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(1, [1.0, 0.0], account_id="a"),
            candidate(2, [0.0, 1.0], account_id="b"),
        ]
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    first = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    assert index.search([1.0, 0.0], 1, filter_a, telemetry=first)[0] == [1]
    assert first.build_performed is True
    assert first.filter_kind == "scalar"
    assert first.filter_cache_hit is False
    assert first.eligible_count == 1
    assert first.index_size == 2
    assert first.build_ms >= 0
    assert first.filter_prepare_ms >= 0
    assert first.gpu_search_ms >= 0

    second = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    assert index.search([1.0, 0.0], 1, filter_a, telemetry=second)[0] == [1]
    assert second.build_performed is False
    assert second.filter_cache_hit is True
    assert second.eligible_count == 1


def test_warmed_cuvs_searches_run_concurrently():
    class ConcurrentRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(4)
            self.active_lock = threading.Lock()
            self.active = 0
            self.peak_active = 0

        def search(self, index, query, limit, mask):
            with self.active_lock:
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
            try:
                self.barrier.wait(timeout=5)
                return super().search(index, query, limit, mask)
            finally:
                with self.active_lock:
                    self.active -= 1

    runtime = ConcurrentRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={"max_concurrent_gpu_searches": 4},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])
    # Build without entering the barrier.
    runtime.barrier = threading.Barrier(1)
    assert index.search([1.0, 0.0], 1, None)[0] == [1]
    runtime.barrier = threading.Barrier(4)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(index.search, [1.0, 0.0], 1, None) for _ in range(4)]
        assert [future.result(timeout=5)[0] for future in futures] == [[1]] * 4

    assert runtime.peak_active == 4


def test_cuvs_gpu_search_gate_serializes_kernels_by_default():
    class SerialRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.active_lock = threading.Lock()
            self.active = 0
            self.peak_active = 0

        def search(self, index, query, limit, mask):
            with self.active_lock:
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
            try:
                time.sleep(0.01)
                return super().search(index, query, limit, mask)
            finally:
                with self.active_lock:
                    self.active -= 1

    runtime = SerialRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])
    assert index.search([1.0, 0.0], 1, None)[0] == [1]

    telemetry = [CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(index.search, [1.0, 0.0], 1, None, None, None, item)
            for item in telemetry
        ]
        assert [future.result(timeout=5)[0] for future in futures] == [[1]] * 4

    assert runtime.peak_active == 1
    assert max(item.queue_ms for item in telemetry) > 0


def test_inflight_search_keeps_immutable_snapshot_during_rebuild():
    class BlockingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.block_next = False
            self.search_started = threading.Event()
            self.resume_search = threading.Event()

        def search(self, index, query, limit, mask):
            if self.block_next:
                self.block_next = False
                self.search_started.set()
                assert self.resume_search.wait(timeout=5)
            return super().search(index, query, limit, mask)

    runtime = BlockingRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={"max_concurrent_gpu_searches": 2},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])
    assert index.search([1.0, 0.0], 1, None)[0] == [1]

    runtime.block_next = True
    with ThreadPoolExecutor(max_workers=1) as executor:
        inflight = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        index.upsert([delta(2, [2.0, 0.0])])
        assert index.search([1.0, 0.0], 1, None)[0] == [2]
        runtime.resume_search.set()
        assert inflight.result(timeout=5)[0] == [1]

    assert runtime.build_count == 2


def test_auto_memory_coordinator_serializes_builds_on_same_device():
    class CoordinatedRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.device_id = 7
            self.first_build_started = threading.Event()
            self.resume_first_build = threading.Event()
            self.build_lock = threading.Lock()
            self.build_attempts = 0
            self.active_builds = 0
            self.peak_active_builds = 0

        def build(self, dataset):
            with self.build_lock:
                self.build_attempts += 1
                attempt = self.build_attempts
                self.active_builds += 1
                self.peak_active_builds = max(self.peak_active_builds, self.active_builds)
            try:
                if attempt == 1:
                    self.first_build_started.set()
                    assert self.resume_first_build.wait(timeout=5)
                return super().build(dataset)
            finally:
                with self.build_lock:
                    self.active_builds -= 1

    runtime = CoordinatedRuntime()

    def make_index(label):
        index = CuVSDenseIndex(
            dimension=2,
            distance="ip",
            normalize_vectors=False,
            field_types={},
            config={
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
            },
            runtime=runtime,
            auto_memory=True,
        )
        index.add_candidates([candidate(label, [1.0, 0.0])])
        return index

    first = make_index(1)
    second = make_index(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first.search, [1.0, 0.0], 1, None)
        assert runtime.first_build_started.wait(timeout=5)
        second_future = executor.submit(second.search, [1.0, 0.0], 1, None)
        runtime.resume_first_build.set()
        assert first_future.result(timeout=5)[0] == [1]
        assert second_future.result(timeout=5)[0] == [2]

    assert runtime.build_attempts == 2
    assert runtime.peak_active_builds == 1


def test_cuvs_l2_scores_match_openviking_score_convention():
    runtime = FakeCuVSRuntime(metric="sqeuclidean")
    index = CuVSDenseIndex(
        dimension=2,
        distance="l2",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [0.0, 0.0]), candidate(2, [2.0, 0.0])])

    labels, scores = index.search([1.0, 0.0], 2, None)
    assert labels == [1, 2]
    assert scores == [0.0, 0.0]  # OpenViking exposes 1 - squared-L2.


def test_cuvs_memory_estimate_accounts_for_fp32_graphs_and_filter_cache():
    estimate = estimate_cuvs_memory(
        vector_count=1_000_000,
        dimension=768,
        algorithm="cagra",
        build_params={"graph_degree": 64, "intermediate_graph_degree": 128},
        filter_cache_size=16,
        safety_factor=2.0,
    )

    assert estimate.vector_bytes == 1_000_000 * 768 * 4
    assert estimate.graph_bytes == 1_000_000 * 64 * 4
    assert estimate.build_graph_bytes == 1_000_000 * 128 * 4
    assert estimate.filter_cache_bytes == ((1_000_000 + 31) // 32) * 4 * 16
    assert estimate.estimated_peak_bytes == 2 * (
        estimate.vector_bytes
        + estimate.graph_bytes
        + estimate.build_graph_bytes
        + estimate.filter_cache_bytes
    )

    fp16 = estimate_cuvs_memory(
        vector_count=1_000_000,
        dimension=768,
        algorithm="brute_force",
        build_params={},
        filter_cache_size=0,
        safety_factor=1.0,
        dtype="float16",
    )
    assert fp16.vector_bytes == 1_000_000 * 768 * 2


def test_cuvs_rejects_unsupported_gpu_dtype():
    with pytest.raises(ValueError, match="dtype"):
        CuVSDenseIndex(
            dimension=2,
            distance="ip",
            normalize_vectors=False,
            field_types={},
            config={"dtype": "int8"},
            runtime=FakeCuVSRuntime(),
        )


def test_auto_cuvs_retries_after_gpu_memory_becomes_available():
    runtime = FakeCuVSRuntime()
    runtime.free_memory_bytes = 15
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={
            "algorithm": "brute_force",
            "filter_cache_size": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(1, [1.0, 0.0]), candidate(2, [0.0, 1.0])])

    rejected = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=True)
    with pytest.raises(CuVSMemoryBudgetError, match="estimated GPU peak"):
        index.search([1.0, 0.0], 1, None, telemetry=rejected)
    assert runtime.build_count == 0
    assert rejected.memory_estimated_peak_bytes == 16
    assert rejected.memory_free_bytes == 15
    assert rejected.memory_usable_bytes == 15

    runtime.free_memory_bytes = 16
    admitted = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=True)
    assert index.search([1.0, 0.0], 1, None, telemetry=admitted)[0] == [1]
    assert runtime.build_count == 1
    assert runtime.release_count == 2
    assert admitted.memory_estimated_peak_bytes == 16
    assert admitted.memory_free_bytes == 16


def test_auto_cuvs_checks_memory_before_materializing_host_dataset():
    runtime = FakeCuVSRuntime()
    runtime.free_memory_bytes = 15
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={
            "algorithm": "brute_force",
            "filter_cache_size": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(1, [1.0, 0.0]), candidate(2, [0.0, 1.0])])
    vector_accesses = []

    class _TrackedRecord:
        fields = {}

        @property
        def vector(self):
            vector_accesses.append(True)
            raise AssertionError("host dataset was materialized before memory admission")

    with index._lock:
        index._records = {label: _TrackedRecord() for label in index._records}

    with pytest.raises(CuVSMemoryBudgetError, match="estimated GPU peak"):
        index.prepare_rebuild()

    assert vector_accesses == []
    assert runtime.build_count == 0


def test_auto_cuvs_converts_gpu_allocation_failure_to_native_fallback_signal():
    class OutOfMemoryRuntime(FakeCuVSRuntime):
        def build(self, _dataset):
            raise RuntimeError("out of memory")

        @staticmethod
        def is_out_of_memory(_exc):
            return True

    runtime = OutOfMemoryRuntime()
    index = CuVSDenseIndex(
        dimension=4,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={
            "filter_cache_size": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(1, [1.0, 0.0, 0.0, 0.0])])

    with pytest.raises(CuVSMemoryBudgetError, match="allocation failure"):
        index.search([1.0, 0.0, 0.0, 0.0], 1, None)
    assert runtime.release_count == 2


def test_filter_cache_reuses_prepared_mask_and_invalidates_on_mutation():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 2},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(1, [1.0, 0.0], account_id="a"),
            candidate(2, [0.0, 1.0], account_id="b"),
        ]
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    assert index.search([1.0, 0.0], 1, filter_a)[0] == [1]
    assert index.search([1.0, 0.0], 1, filter_a)[0] == [1]
    assert runtime.prepare_filter_count == 1

    index.upsert([delta(2, [0.0, 1.0], account_id="a")])
    assert index.search([0.0, 1.0], 1, filter_a)[0] == [2]
    assert runtime.prepare_filter_count == 2

    index.delete([DeltaRecord(label=1)])
    assert index.search([1.0, 0.0], 2, filter_a)[0] == [2]
    assert runtime.prepare_filter_count == 3


def test_native_filter_resolver_projects_bitset_in_cuvs_row_order():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 2},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(30, [0.0, 1.0], account_id="b"),
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.5, 0.5], account_id="a"),
        ]
    )
    calls = []

    def register(ordered_labels):
        calls.append(("register", list(ordered_labels)))

    def resolve(filters):
        calls.append(("resolve", filters))
        # Rows 1 and 2 are eligible in the cuVS dataset order above.
        return [0b110], 2

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    assert index.search([1.0, 0.0], 3, filter_a, resolve, register)[0] == [10, 20]
    assert index.search([1.0, 0.0], 3, filter_a, resolve, register)[0] == [10, 20]
    assert calls == [("register", [30, 10, 20]), ("resolve", filter_a)]
    # Native packed words bypass the Python predicate/mask packer.
    assert runtime.prepare_filter_count == 0


def test_auto_mode_caches_native_route_for_selective_filter():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.0, 1.0], account_id="b"),
        ]
    )
    calls = []

    def register(ordered_labels):
        calls.append(("register", list(ordered_labels)))

    def resolve(filters):
        calls.append(("resolve", filters))
        return [0b01], 1

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    for _ in range(2):
        with pytest.raises(CuVSNativeRouteError, match="1 candidates"):
            index.search([1.0, 0.0], 1, filter_a, resolve, register)

    assert calls == [("register", [10, 20]), ("resolve", filter_a)]
    # Selectivity is decided before GPU admission/build, even while dirty.
    assert runtime.build_count == 0
    assert runtime.release_count == 0
    assert runtime.prepare_filter_count == 0


def test_auto_mode_preflights_different_filters_concurrently():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.0, 1.0], account_id="b"),
        ]
    )
    barrier = threading.Barrier(4)
    active_lock = threading.Lock()
    active = 0
    peak_active = 0
    registered_layouts = []

    def register(ordered_labels):
        registered_layouts.append(list(ordered_labels))

    def resolve(_filters):
        nonlocal active, peak_active
        with active_lock:
            active += 1
            peak_active = max(peak_active, active)
        barrier.wait(timeout=5)
        with active_lock:
            active -= 1
        return [0b01], 1

    filters = [
        {"op": "must", "field": "account_id", "conds": [value]} for value in ("a", "b", "c", "d")
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(index.preflight_native_count, item, resolve, register)
            for item in filters
        ]
        routes = [future.result(timeout=5) for future in futures]

    assert routes == [1] * 4
    assert peak_active == 4
    assert registered_layouts == [[10, 20]]


def test_auto_mode_does_not_cache_preflight_across_record_change():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(10, [1.0, 0.0], account_id="a")])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    resolver_started = threading.Event()
    resume_resolver = threading.Event()

    def resolve(_filters):
        resolver_started.set()
        assert resume_resolver.wait(timeout=5)
        return [0b1], 1

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            index.preflight_native_count,
            filter_a,
            resolve,
            lambda _labels: None,
        )
        assert resolver_started.wait(timeout=5)
        index.add_candidates([candidate(20, [0.0, 1.0], account_id="b")])
        resume_resolver.set()
        assert future.result(timeout=5) is None

    cache_miss_observed = threading.Event()

    def resolve_after_change(_filters):
        cache_miss_observed.set()
        return [0b11], 2

    assert (
        index.preflight_native_count(
            filter_a,
            resolve_after_change,
            lambda _labels: None,
        )
        is None
    )
    assert cache_miss_observed.is_set()


def test_auto_mode_selective_filter_skips_rebuild_after_mutation():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.0, 1.0], account_id="b"),
        ]
    )
    registered_layouts = []

    def register(ordered_labels):
        registered_layouts.append(list(ordered_labels))

    assert index.search([1.0, 0.0], 1, None, None, register)[0] == [10]
    assert runtime.build_count == 1

    index.upsert([delta(20, [2.0, 0.0], account_id="b")])

    def resolve(_filters):
        return [0b10], 1

    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}
    with pytest.raises(CuVSNativeRouteError, match="1 candidates"):
        index.search([1.0, 0.0], 1, filter_b, resolve, register)

    # The stale GPU snapshot is not rebuilt for a query routed to native.
    assert runtime.build_count == 1
    assert registered_layouts == [[10, 20], [10, 20]]

    # The next GPU-routed query still observes dirty state and rebuilds once.
    assert index.search([1.0, 0.0], 1, None, None, register)[0] == [20]
    assert runtime.build_count == 2


def test_auto_mode_empty_filter_result_skips_initial_gpu_build():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(10, [1.0, 0.0], account_id="a")])

    assert index.search(
        [1.0, 0.0],
        1,
        {"op": "must", "field": "account_id", "conds": ["missing"]},
        lambda _filters: ([0], 0),
        lambda _labels: None,
    ) == ([], [])
    assert runtime.build_count == 0
    assert runtime.release_count == 0


def test_auto_mode_wide_filter_reuses_preflight_bitmap_after_build():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.0, 1.0], account_id="a"),
        ]
    )
    resolve_count = 0

    def resolve(_filters):
        nonlocal resolve_count
        resolve_count += 1
        return [0b11], 2

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    assert (
        index.preflight_native_count(
            filter_a,
            resolve,
            lambda _labels: None,
        )
        is None
    )
    labels, _ = index.search(
        [1.0, 0.0],
        2,
        filter_a,
        resolve,
        lambda _labels: None,
    )
    assert labels == [10, 20]
    assert resolve_count == 1
    assert runtime.build_count == 1


def test_auto_mode_retains_native_filter_token_for_selective_route():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"auto_filter_native_threshold": 1},
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(10, [1.0, 0.0], account_id="a")])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    assert (
        index.preflight_native_count(
            filter_a,
            lambda _filters: ([0b1], 1, 17),
            lambda _labels: None,
        )
        == 1
    )
    assert index.native_filter_token(filter_a) == 17


def test_auto_mode_uses_lower_native_threshold_for_path_filters():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"uri": "path"},
        config={
            "auto_filter_native_threshold": 2,
            "auto_path_filter_native_threshold": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], uri="/docs/one"),
            candidate(20, [0.0, 1.0], uri="/other/two"),
        ]
    )

    def resolve(_filters):
        return [0b01], 1

    path_filter = {
        "op": "must",
        "field": "uri",
        "conds": ["/docs"],
        "para": "-d=-1",
    }
    assert index.search([1.0, 0.0], 1, path_filter, resolve, lambda _labels: None)[0] == [10]


def test_filter_cache_uses_lru_bound():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 1},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(1, [1.0, 0.0], account_id="a"),
            candidate(2, [0.0, 1.0], account_id="b"),
        ]
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}

    index.search([1.0, 0.0], 1, filter_a)
    index.search([0.0, 1.0], 1, filter_b)
    index.search([1.0, 0.0], 1, filter_a)

    assert runtime.prepare_filter_count == 3


def test_filter_evaluator_covers_lists_ranges_contains_and_path_depth():
    fields = {
        "tags": ["a", "b"],
        "count": 7,
        "title": "cuVS integration",
        "uri": "docs/deep/item",
    }
    field_types = {
        "tags": "list<string>",
        "count": "int64",
        "title": "string",
        "uri": "path",
    }
    node = {
        "op": "and",
        "conds": [
            {"op": "must", "field": "tags", "conds": ["b"]},
            {"op": "range", "field": "count", "gte": 5, "lt": 10},
            {"op": "contains", "field": "title", "substring": "cuVS"},
            {"op": "must", "field": "uri", "conds": ["/docs"], "para": "-d=2"},
        ],
    }
    assert matches_filter(fields, node, field_types)
    node["conds"][-1]["para"] = "-d=1"
    assert not matches_filter(fields, node, field_types)


def test_filter_evaluator_rejects_type_sensitive_filters_for_native_fallback():
    with pytest.raises(UnsupportedCuVSFilterError):
        matches_filter(
            {"created_at": "2026-07-02T00:00:00Z"},
            {"op": "range", "field": "created_at", "gte": "2026-07-01T00:00:00Z"},
            {"created_at": "date_time"},
        )


def test_dimension_mismatch_is_reported_before_runtime_call():
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=FakeCuVSRuntime(),
    )
    with pytest.raises(ValueError, match="dimension mismatch"):
        index.add_candidates([candidate(1, [1.0, 2.0, 3.0])])


def test_missing_cuvs_runtime_has_actionable_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def import_without_cupy(name, *args, **kwargs):
        if name == "cupy":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_cupy)
    with pytest.raises(CuVSUnavailableError, match="cuvs-cu12 or cuvs-cu13"):
        CuVSDenseIndex(
            dimension=2,
            distance="ip",
            normalize_vectors=False,
            field_types={},
            config={},
        )
