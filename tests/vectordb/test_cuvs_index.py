# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest

from openviking.storage.vectordb.index import cuvs_index as cuvs_index_module
from openviking.storage.vectordb.index.cuvs_index import (
    CuVSDenseIndex,
    CuVSMemoryBudgetError,
    CuVSNativeRouteError,
    CuVSSearchTelemetry,
    CuVSUnavailableError,
    UnsupportedCuVSFilterError,
    _CuVSRuntime,
    _PackedFP32Rows,
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
        self.close_count = 0
        self.close_error = None
        self.search_batch_sizes = []

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

    def search_batch(self, index, queries, limit, mask):
        self.search_batch_sizes.append(len(queries))
        results = [self.search(index, query, limit, mask) for query in queries]
        return [result[0] for result in results], [result[1] for result in results]

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
        self.close_count += 1
        if self.close_error is not None:
            error = self.close_error
            self.close_error = None
            raise error
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
            self.fail_host_copy = False

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
            if spy.fail_host_copy:
                raise RuntimeError("injected host copy failure")
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
            query_count = len(_queries.values)
            return FakeArray([[0.25]] * query_count, "float32", "distances"), FakeArray(
                [[0]] * query_count, "int64", "neighbors"
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
    # The default serialized setting must still use an explicit synchronized
    # resource.  This is the production shape used when benchmark callers
    # churn worker threads while GPU admission remains at one search.
    runtime.set_max_concurrent_searches(1)
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
        assert runtime.search_batch(
            runtime_index,
            [[1.0, 0.0], [0.0, 1.0]],
            1,
            [True],
        ) == ([[0], [0]], [[0.25], [0.25]])
        assert runtime.search(runtime_index, [1.0, 0.0], 1, [True]) == ([0], [0.25])
        spy.fail_host_copy = True
        with pytest.raises(RuntimeError, match="injected host copy failure"):
            runtime.search(runtime_index, [1.0, 0.0], 1, [True])
        spy.fail_host_copy = False
        assert spy.releases == [("resources-1", 3), ("resources-2", 3)]
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
    assert spy.resource_count == 3
    assert len(runtime._owned_resources) <= 1
    assert len(runtime._available_resources) == 1

    # Search and post-sync host-copy failures discarded the first two resources
    # under device 3. The bounded pool reuses the replacement across
    # short-lived threads and retains it until close can also destroy it on the
    # captured device.
    assert spy.releases == [("resources-1", 3), ("resources-2", 3)]
    spy.set_current(9)
    runtime.close()
    assert spy.current() == 9
    assert spy.releases == [
        ("resources-1", 3),
        ("resources-2", 3),
        ("resources-3", 3),
    ]

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


def wait_for_preflight_participants(index, expected, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with index._lock:
            if any(flight.participants >= expected for flight in index._preflight_flights.values()):
                return
        time.sleep(0.001)
    raise AssertionError(f"Timed out waiting for {expected} preflight participants")


class _PreflightAbort(BaseException):
    """Model an asynchronous owner abort that ordinary Exception misses."""


def test_cuvs_host_shadow_is_immutable_compact_fp32():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={"dtype": "float16"},
        runtime=runtime,
    )
    source = [0.1, -3.25]
    index.add_candidates([candidate(10, source), candidate(20, [2.0, 4.0])])

    assert index.host_shadow_nbytes == 2 * 2 * 4
    stored = index._records[10].vector
    assert isinstance(stored, bytes)
    assert len(stored) == 2 * 4
    assert list(memoryview(stored).cast("f")) == pytest.approx([0.1, -3.25])

    # The host snapshot owns FP32 values instead of references to caller-owned
    # Python floats, even when the configured device dataset uses FP16.
    source[:] = [9.0, 9.0]
    rebuild = index.prepare_rebuild()
    assert rebuild is not None
    assert rebuild.labels == (10, 20)
    assert runtime.dataset[0] == pytest.approx([0.1, -3.25])
    assert index.commit_rebuild(rebuild)

    # Updating an existing label keeps dict/row order; delete plus reinsert
    # retains the previous append-at-end behavior.
    index.upsert([delta(10, [5.0, 6.0])])
    replacement = index.prepare_rebuild()
    assert replacement is not None
    assert replacement.labels == (10, 20)
    assert runtime.dataset[0] == pytest.approx([5.0, 6.0])
    assert index.commit_rebuild(replacement)
    index.delete([DeltaRecord(label=10)])
    index.upsert([delta(10, [7.0, 8.0])])
    reinserted = index.prepare_rebuild()
    assert reinserted is not None
    assert reinserted.labels == (20, 10)
    assert runtime.dataset[1] == pytest.approx([7.0, 8.0])

    index.close()
    assert index.size == 0
    assert index.host_shadow_nbytes == 0
    assert runtime.closed


@pytest.mark.parametrize(
    ("dtype", "expected_upload_dtype"),
    [("float32", "float32"), ("float16", "float16")],
)
def test_cuvs_runtime_uploads_packed_rows_in_bounded_batches(
    monkeypatch, dtype, expected_upload_dtype
):
    class DeviceDataset:
        def __init__(self, shape, device_dtype):
            self.rows = [[None] * shape[1] for _ in range(shape[0])]
            self.dtype = device_dtype
            self.upload_dtypes = []

        def __getitem__(self, row_slice):
            owner = self

            class DeviceSlice:
                def set(self, host_rows):
                    owner.upload_dtypes.append(str(host_rows.dtype))
                    owner.rows[row_slice] = host_rows.tolist()

            return DeviceSlice()

    class FakeCuPy:
        float16 = "float16"
        float32 = "float32"

        @staticmethod
        def empty(shape, dtype):
            return DeviceDataset(shape, dtype)

    class FakeBruteForce:
        @staticmethod
        def build(dataset, metric):
            assert metric == "inner_product"
            return dataset

    runtime = _CuVSRuntime.__new__(_CuVSRuntime)
    runtime.cp = FakeCuPy()
    runtime.device_scope = lambda: nullcontext()
    runtime.device_dtype = FakeCuPy.float16 if dtype == "float16" else FakeCuPy.float32
    runtime.dtype = dtype
    runtime.algorithm = "brute_force"
    runtime.metric = "inner_product"
    runtime.brute_force = FakeBruteForce()
    runtime.cagra = object()
    runtime.build_params = {}

    rows = _PackedFP32Rows(
        tuple(array("f", values).tobytes() for values in ([1, 2], [3, 4], [5, 6])),
        dimension=2,
    )
    monkeypatch.setattr(cuvs_index_module, "_FP32_UPLOAD_BATCH_BYTES", 8)
    built = runtime.build(rows)

    assert built.dataset.rows == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    assert built.dataset.upload_dtypes == [expected_upload_dtype] * 3
    assert rows.nbytes == 3 * 2 * 4
    assert [end - start for start, end, _payload in rows.iter_packed_batches(8)] == [1, 1, 1]


def test_mutation_during_packed_rebuild_keeps_candidate_snapshot_immutable():
    class BlockingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.first_build_started = threading.Event()
            self.resume_first_build = threading.Event()

        def build(self, dataset):
            if self.build_count == 0:
                self.first_build_started.set()
                assert self.resume_first_build.wait(timeout=5)
            return super().build(dataset)

    runtime = BlockingRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0])])

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(index.prepare_rebuild)
        assert runtime.first_build_started.wait(timeout=5)
        index.upsert([delta(1, [0.0, 1.0])])
        runtime.resume_first_build.set()
        stale = pending.result(timeout=5)

    assert stale is not None
    assert runtime.dataset == [[1.0, 0.0]]
    assert index.commit_rebuild(stale) is False
    replacement = index.prepare_rebuild()
    assert replacement is not None
    assert runtime.dataset == [[0.0, 1.0]]
    assert index.commit_rebuild(replacement)


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


def test_prepared_filter_lru_and_close_release_on_captured_device():
    class DeviceOwnedFilter:
        def __init__(self, runtime, name, values):
            self.runtime = runtime
            self.name = name
            self.values = values

        def __getitem__(self, index):
            return self.values[index]

        def __del__(self):
            self.runtime.released.append((self.name, self.runtime.current_device))

    class LifecycleRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.current_device = 9
            self.prepared_count = 0
            self.released = []

        def device_scope(self):
            runtime = self

            class DeviceContext:
                def __enter__(self):
                    self.previous = runtime.current_device
                    runtime.current_device = 3

                def __exit__(self, _exc_type, _exc, _traceback):
                    runtime.current_device = self.previous

            return DeviceContext()

        def prepare_filter_words(self, words):
            with self.device_scope():
                self.prepared_count += 1
                values = tuple(bool(words[0] & (1 << row)) for row in range(2))
                return DeviceOwnedFilter(self, f"filter-{self.prepared_count}", values)

        def close(self):
            assert self.current_device == 3
            super().close()

    runtime = LifecycleRuntime()
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

    def resolve(filters):
        return ([0b01], 1) if filters["conds"][0] == "a" else ([0b10], 1)

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}

    def registrar(_labels):
        return None

    assert index.search([1.0, 0.0], 1, filter_a, resolve, registrar)[0] == [1]
    assert runtime.released == []
    assert index.search([0.0, 1.0], 1, filter_b, resolve, registrar)[0] == [2]
    assert runtime.released == [("filter-1", 3)]

    index.close()
    assert runtime.released == [("filter-1", 3), ("filter-2", 3)]
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
    assert max(item.gpu_gate_queue_ms for item in telemetry) > 0
    assert all(item.queue_ms >= item.gpu_gate_queue_ms for item in telemetry)


def test_native_filter_resolution_runs_before_gpu_search_admission():
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
        field_types={"account_id": "string"},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], account_id="a")])
    assert index.search([1.0, 0.0], 1, None)[0] == [1]

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    resolver_ran = threading.Event()
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    def resolve(_filters):
        resolver_ran.set()
        return [0b1], 1

    runtime.block_next = True
    with ThreadPoolExecutor(max_workers=2) as executor:
        occupying = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        filtered = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            filter_a,
            resolve,
            lambda _labels: None,
            telemetry,
        )
        # The resolver is host work and must complete while the sole GPU
        # admission permit is still occupied by the first search.
        assert resolver_ran.wait(timeout=5)
        runtime.resume_search.set()
        assert occupying.result(timeout=5)[0] == [1]
        assert filtered.result(timeout=5)[0] == [1]

    assert telemetry.gpu_gate_queue_ms > 0
    assert telemetry.queue_ms >= telemetry.gpu_gate_queue_ms


def test_explicit_same_filter_host_resolution_is_singleflight():
    class WordPreparingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.prepare_filter_words_count = 0

        def prepare_filter_words(self, words):
            self.prepare_filter_words_count += 1
            return (bool(words[0] & 1),)

    runtime = WordPreparingRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 2},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], account_id="a")])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    start = threading.Barrier(2)
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    resolver_calls = 0

    def resolve(_filters):
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        assert release_resolver.wait(timeout=5)
        return [0b1], 1

    def invoke():
        start.wait(timeout=5)
        return index.search(
            [1.0, 0.0],
            1,
            filter_a,
            resolve,
            lambda _labels: None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke) for _ in range(2)]
        assert resolver_started.wait(timeout=5)
        wait_for_preflight_participants(index, 2)
        release_resolver.set()
        assert [future.result(timeout=5)[0] for future in futures] == [[1], [1]]

    assert resolver_calls == 1
    assert runtime.prepare_filter_words_count == 1
    assert not index._preflight_flights


def test_mutation_discards_host_filter_resolved_for_old_generation():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 2},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], account_id="old")])
    filter_new = {"op": "must", "field": "account_id", "conds": ["new"]}
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    resolver_calls = 0
    registered_layouts = []

    def resolve(_filters):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 1:
            resolver_started.set()
            assert release_resolver.wait(timeout=5)
            return [0b0], 0
        return [0b10], 1

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            index.search,
            [0.0, 1.0],
            1,
            filter_new,
            resolve,
            lambda labels: registered_layouts.append(list(labels)),
        )
        assert resolver_started.wait(timeout=5)
        index.add_candidates([candidate(2, [0.0, 1.0], account_id="new")])
        release_resolver.set()
        assert future.result(timeout=5)[0] == [2]

    assert resolver_calls == 2
    assert registered_layouts == [[1], [1, 2]]
    assert runtime.build_count == 1


def test_close_during_host_filter_resolution_rejects_search_before_gpu_admission():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], account_id="a")])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    resolver_started = threading.Event()
    release_resolver = threading.Event()

    def resolve(_filters):
        resolver_started.set()
        assert release_resolver.wait(timeout=5)
        return [0b1], 1

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            filter_a,
            resolve,
            lambda _labels: None,
        )
        assert resolver_started.wait(timeout=5)
        index.close()
        release_resolver.set()
        with pytest.raises(RuntimeError, match="dense index is closed"):
            future.result(timeout=5)

    assert runtime.build_count == 0
    assert runtime.closed


def test_close_while_warmed_filter_waits_for_gpu_gate_does_not_borrow_device_cache():
    class BlockingWordRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.block_next = False
            self.search_started = threading.Event()
            self.resume_search = threading.Event()

        def prepare_filter_words(self, words):
            return (bool(words[0] & 1),)

        def search(self, index, query, limit, mask):
            if self.block_next:
                self.block_next = False
                self.search_started.set()
                assert self.resume_search.wait(timeout=5)
            return super().search(index, query, limit, mask)

    runtime = BlockingWordRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], account_id="a")])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    def resolver(_filters):
        return [0b1], 1

    def registrar(_labels):
        return None

    assert index.search([1.0, 0.0], 1, filter_a, resolver, registrar)[0] == [1]

    host_prepared = threading.Event()
    original_prepare = index._prepare_host_filter

    def observe_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        host_prepared.set()
        return prepared

    index._prepare_host_filter = observe_prepare
    runtime.block_next = True
    with ThreadPoolExecutor(max_workers=3) as executor:
        occupying = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        waiting = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            filter_a,
            resolver,
            registrar,
        )
        assert host_prepared.wait(timeout=5)

        closing = executor.submit(index.close)
        deadline = time.monotonic() + 5
        while not index._closed and time.monotonic() < deadline:
            time.sleep(0.001)
        assert index._closed

        runtime.resume_search.set()
        assert occupying.result(timeout=5)[0] == [1]
        with pytest.raises(RuntimeError, match="dense index is closed"):
            waiting.result(timeout=5)
        closing.result(timeout=5)

    assert runtime.closed


def test_device_cache_eviction_before_admission_uses_one_in_gate_fallback():
    class WordPreparingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.prepare_filter_words_count = 0

        def prepare_filter_words(self, words):
            self.prepare_filter_words_count += 1
            return tuple(bool(words[0] & (1 << row)) for row in range(2))

    runtime = WordPreparingRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 1, "max_concurrent_gpu_searches": 2},
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
    resolver_calls = {"a": 0, "b": 0}

    def resolve(filters):
        account_id = filters["conds"][0]
        resolver_calls[account_id] += 1
        return ([0b01], 1) if account_id == "a" else ([0b10], 1)

    def registrar(_labels):
        return None

    # Warm A so the delayed search below initially observes a device-cache hit.
    assert index.search([1.0, 0.0], 1, filter_a, resolve, registrar)[0] == [1]
    assert resolver_calls == {"a": 1, "b": 0}

    a_host_prepared = threading.Event()
    release_a = threading.Event()
    original_prepare = index._prepare_host_filter

    def pause_a_after_host_prepare(filters, *args, **kwargs):
        prepared = original_prepare(filters, *args, **kwargs)
        if filters == filter_a:
            a_host_prepared.set()
            assert release_a.wait(timeout=5)
        return prepared

    index._prepare_host_filter = pause_a_after_host_prepare
    eviction_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    with ThreadPoolExecutor(max_workers=1) as executor:
        delayed_a = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            filter_a,
            resolve,
            registrar,
            eviction_telemetry,
        )
        assert a_host_prepared.wait(timeout=5)

        # Materializing B evicts A from the size-one device LRU after A's host
        # context was captured but before A consumes an admission permit.
        assert index.search([0.0, 1.0], 1, filter_b, resolve, registrar)[0] == [2]
        release_a.set()
        assert delayed_a.result(timeout=5)[0] == [1]

    # A resolves once to warm and exactly once in the admitted eviction
    # fallback; it does not loop under alternating hot-key churn.
    assert resolver_calls == {"a": 2, "b": 1}
    assert runtime.prepare_filter_words_count == 3
    assert eviction_telemetry.filter_cache_hit is False
    assert eviction_telemetry.filter_cache_eviction_fallback is True
    assert eviction_telemetry.as_dict()["filter_cache_eviction_fallback"] is True


def make_micro_batch_index(runtime, **config):
    dense_config = {
        "micro_batching_enabled": True,
        "micro_batching_max_batch_size": 4,
        "micro_batching_max_wait_ms": 50.0,
        **config,
    }
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config=dense_config,
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(1, [1.0, 0.0], account_id="a"),
            candidate(2, [0.0, 1.0], account_id="b"),
            candidate(3, [0.8, 0.2], account_id="a"),
        ]
    )
    # Exclude the first lazy build and its unavoidable singleton from assertions.
    assert index.search([1.0, 0.0], 1, None)[0] == [1]
    runtime.search_batch_sizes.clear()
    return index


class BlockingMicroBatchRuntime(FakeCuVSRuntime):
    def __init__(self):
        super().__init__()
        self.block_next_batch = False
        self.search_started = threading.Event()
        self.release_search = threading.Event()

    def search_batch(self, index, queries, limit, mask):
        if self.block_next_batch:
            self.block_next_batch = False
            self.search_started.set()
            assert self.release_search.wait(timeout=5)
        return super().search_batch(index, queries, limit, mask)


def wait_for_warm_lookahead(index, expected):
    with index._micro_batch_condition:
        assert index._micro_batch_condition.wait_for(
            lambda: index._micro_batch_warm_lookahead == expected,
            timeout=5,
        )


def run_simultaneous_searches(index, requests):
    barrier = threading.Barrier(len(requests) + 1)

    def run(request):
        barrier.wait(timeout=5)
        return index.search(*request)

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = [executor.submit(run, request) for request in requests]
        barrier.wait(timeout=5)
        return [future.result(timeout=5) for future in futures]


def test_cuvs_micro_batch_coalesces_compatible_queries_and_demuxes_rows():
    runtime = FakeCuVSRuntime()
    index = make_micro_batch_index(runtime)
    telemetry = [CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False) for _ in range(4)]
    requests = [
        ([1.0, 0.0], 1, None, None, None, telemetry[0]),
        ([0.0, 1.0], 1, None, None, None, telemetry[1]),
        ([0.9, 0.1], 1, None, None, None, telemetry[2]),
        ([0.1, 0.9], 1, None, None, None, telemetry[3]),
    ]

    results = run_simultaneous_searches(index, requests)

    assert [result[0] for result in results] == [[1], [2], [1], [2]]
    assert runtime.search_batch_sizes == [4]
    assert all(item.micro_batching_enabled for item in telemetry)
    assert [item.batch_size for item in telemetry] == [4] * 4
    assert all(item.batch_wait_ms >= 0.0 for item in telemetry)
    index.close()


def test_cuvs_micro_batch_warm_fast_path_fills_behind_active_batch():
    runtime = BlockingMicroBatchRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_batch_size=2,
        micro_batching_max_wait_ms=100.0,
    )
    runtime.block_next_batch = True
    telemetry = [CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False) for _ in range(3)]

    with ThreadPoolExecutor(max_workers=3) as executor:
        active = executor.submit(index.search, [1.0, 0.0], 1, None, None, None, telemetry[0])
        assert runtime.search_started.wait(timeout=5)
        lookahead = [
            executor.submit(index.search, query, 1, None, None, None, item)
            for query, item in zip(
                ([1.0, 0.0], [0.0, 1.0]),
                telemetry[1:],
                strict=True,
            )
        ]
        wait_for_warm_lookahead(index, 2)

        assert all(not future.done() for future in lookahead)
        assert index._active_searches == 3
        assert all(item.micro_batching_warm_fast_path for item in telemetry)
        assert all(item.gpu_gate_queue_ms == 0.0 for item in telemetry)

        runtime.release_search.set()
        assert active.result(timeout=5)[0] == [1]
        assert [future.result(timeout=5)[0] for future in lookahead] == [[1], [2]]

    assert runtime.search_batch_sizes == [1, 2]
    assert [item.batch_size for item in telemetry] == [1, 2, 2]
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_warm_fast_path_caps_one_batch_and_preserves_progress():
    runtime = BlockingMicroBatchRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_batch_size=2,
        micro_batching_max_wait_ms=100.0,
    )
    runtime.block_next_batch = True
    warm_telemetry = [
        CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False) for _ in range(2)
    ]
    overflow_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    with ThreadPoolExecutor(max_workers=4) as executor:
        active = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        warm = [
            executor.submit(index.search, query, 1, None, None, None, item)
            for query, item in zip(
                ([1.0, 0.0], [0.0, 1.0]),
                warm_telemetry,
                strict=True,
            )
        ]
        wait_for_warm_lookahead(index, 2)
        overflow = executor.submit(
            index.search,
            [0.9, 0.1],
            1,
            None,
            None,
            None,
            overflow_telemetry,
        )

        assert not overflow.done()
        assert index._micro_batch_warm_lookahead == 2
        assert all(item.micro_batching_warm_fast_path for item in warm_telemetry)
        assert overflow_telemetry.micro_batching_warm_fast_path is False

        runtime.release_search.set()
        assert active.result(timeout=5)[0] == [1]
        assert [future.result(timeout=5)[0] for future in warm] == [[1], [2]]
        assert overflow.result(timeout=5)[0] == [1]

    assert runtime.search_batch_sizes == [1, 2, 1]
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_empty_filter_uses_warm_fast_path():
    runtime = BlockingMicroBatchRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_batch_size=2,
        micro_batching_max_wait_ms=100.0,
    )
    runtime.block_next_batch = True
    no_filter_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    empty_filter_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    with ThreadPoolExecutor(max_workers=3) as executor:
        active = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        no_filter = executor.submit(
            index.search,
            [0.0, 1.0],
            1,
            None,
            None,
            None,
            no_filter_telemetry,
        )
        wait_for_warm_lookahead(index, 1)
        empty_filter = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            {},
            None,
            None,
            empty_filter_telemetry,
        )
        wait_for_warm_lookahead(index, 2)

        assert no_filter_telemetry.micro_batching_warm_fast_path is True
        assert empty_filter_telemetry.micro_batching_warm_fast_path is True
        assert not empty_filter.done()

        runtime.release_search.set()
        assert active.result(timeout=5)[0] == [1]
        assert no_filter.result(timeout=5)[0] == [2]
        assert empty_filter.result(timeout=5)[0] == [1]

    assert runtime.search_batch_sizes == [1, 2]
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_groups_same_filter_but_splits_incompatible_keys():
    runtime = FakeCuVSRuntime()
    index = make_micro_batch_index(runtime)
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}

    same_filter = run_simultaneous_searches(
        index,
        [
            ([1.0, 0.0], 1, filter_a),
            ([0.0, 1.0], 1, filter_a),
        ],
    )
    assert [result[0] for result in same_filter] == [[1], [3]]
    assert runtime.search_batch_sizes == [2]

    runtime.search_batch_sizes.clear()
    split_filters = run_simultaneous_searches(
        index,
        [
            ([1.0, 0.0], 1, filter_a),
            ([0.0, 1.0], 1, filter_b),
        ],
    )
    assert [result[0] for result in split_filters] == [[1], [2]]
    assert sorted(runtime.search_batch_sizes) == [1, 1]

    runtime.search_batch_sizes.clear()
    split_limits = run_simultaneous_searches(
        index,
        [
            ([1.0, 0.0], 1, None),
            ([1.0, 0.0], 2, None),
        ],
    )
    assert [len(result[0]) for result in split_limits] == [1, 2]
    assert sorted(runtime.search_batch_sizes) == [1, 1]
    index.close()


def test_cuvs_micro_batch_warm_fast_path_preserves_filter_and_limit_keys():
    runtime = BlockingMicroBatchRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_batch_size=4,
        micro_batching_max_wait_ms=20.0,
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}

    def resolve(filters):
        return ([0b101], 2) if filters == filter_a else ([0b010], 1)

    def registrar(_labels):
        return None

    assert index.search([1.0, 0.0], 1, filter_a, resolve, registrar)[0] == [1]
    assert index.search([0.0, 1.0], 1, filter_b, resolve, registrar)[0] == [2]
    runtime.search_batch_sizes.clear()
    runtime.block_next_batch = True
    telemetry = [CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False) for _ in range(4)]

    with ThreadPoolExecutor(max_workers=5) as executor:
        active = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        requests = [
            executor.submit(
                index.search, [1.0, 0.0], 1, filter_a, resolve, registrar, telemetry[0]
            ),
            executor.submit(
                index.search, [0.0, 1.0], 1, filter_a, resolve, registrar, telemetry[1]
            ),
            executor.submit(
                index.search, [1.0, 0.0], 2, filter_a, resolve, registrar, telemetry[2]
            ),
            executor.submit(
                index.search, [0.0, 1.0], 1, filter_b, resolve, registrar, telemetry[3]
            ),
        ]
        wait_for_warm_lookahead(index, 4)

        assert all(item.micro_batching_warm_fast_path for item in telemetry)
        assert all(item.filter_cache_hit for item in telemetry)
        runtime.release_search.set()

        assert active.result(timeout=5)[0] == [1]
        results = [future.result(timeout=5)[0] for future in requests]

    assert results == [[1], [3], [1, 3], [2]]
    assert runtime.search_batch_sizes == [1, 2, 1, 1]
    assert [item.batch_size for item in telemetry] == [2, 2, 1, 1]
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_serializes_device_filter_preparation_with_search():
    class BlockingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.block_next_batch = False
            self.search_started = threading.Event()
            self.release_search = threading.Event()
            self.filter_prepared = threading.Event()

        def search_batch(self, index, queries, limit, mask):
            if self.block_next_batch:
                self.block_next_batch = False
                self.search_started.set()
                assert self.release_search.wait(timeout=5)
            return super().search_batch(index, queries, limit, mask)

        def prepare_filter_words(self, _words):
            self.filter_prepared.set()
            return (True, False, True)

    runtime = BlockingRuntime()
    index = make_micro_batch_index(runtime)
    runtime.block_next_batch = True
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    resolver_ran = threading.Event()
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    def resolve(_filters):
        resolver_ran.set()
        return [0b101], 2

    with ThreadPoolExecutor(max_workers=2) as executor:
        active_search = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        filtered_search = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            filter_a,
            resolve,
            lambda _labels: None,
            telemetry,
        )
        try:
            # Native filter resolution remains host work outside admission.
            assert resolver_ran.wait(timeout=5)
            # Device materialization waits for the occupied GPU gate.
            assert not runtime.filter_prepared.wait(timeout=0.1)
        finally:
            runtime.release_search.set()
        assert active_search.result(timeout=5)[0] == [1]
        assert filtered_search.result(timeout=5)[0] == [1]

    assert runtime.filter_prepared.is_set()
    assert telemetry.micro_batching_warm_fast_path is False
    index.close()


def test_cuvs_micro_batch_serializes_rebuild_with_search():
    class BlockingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.block_next_batch = False
            self.observe_build = False
            self.search_started = threading.Event()
            self.release_search = threading.Event()
            self.build_started = threading.Event()

        def search_batch(self, index, queries, limit, mask):
            if self.block_next_batch:
                self.block_next_batch = False
                self.search_started.set()
                assert self.release_search.wait(timeout=5)
            return super().search_batch(index, queries, limit, mask)

        def build(self, dataset):
            if self.observe_build:
                self.build_started.set()
            return super().build(dataset)

    runtime = BlockingRuntime()
    index = make_micro_batch_index(runtime)
    runtime.block_next_batch = True
    runtime.observe_build = True
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        active_search = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        index.upsert([delta(4, [2.0, 0.0], account_id="a")])
        rebuilding_search = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            None,
            None,
            None,
            telemetry,
        )
        try:
            assert not runtime.build_started.wait(timeout=0.1)
        finally:
            runtime.release_search.set()
        assert active_search.result(timeout=5)[0] == [1]
        assert rebuilding_search.result(timeout=5)[0] == [4]

    assert runtime.build_started.is_set()
    assert telemetry.micro_batching_warm_fast_path is False
    index.close()


def test_cuvs_micro_batch_device_lru_eviction_falls_back_once():
    class WordPreparingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.prepare_filter_words_count = 0

        def prepare_filter_words(self, words):
            self.prepare_filter_words_count += 1
            return tuple(bool(words[0] & (1 << row)) for row in range(3))

    runtime = WordPreparingRuntime()
    index = make_micro_batch_index(runtime, filter_cache_size=1)
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}
    resolver_calls = {"a": 0, "b": 0}

    def resolve(filters):
        account_id = filters["conds"][0]
        resolver_calls[account_id] += 1
        return ([0b101], 2) if account_id == "a" else ([0b010], 1)

    def registrar(_labels):
        return None

    assert index.search([1.0, 0.0], 1, filter_a, resolve, registrar)[0] == [1]
    assert resolver_calls == {"a": 1, "b": 0}

    a_host_prepared = threading.Event()
    release_a = threading.Event()
    original_prepare = index._prepare_host_filter

    def pause_a_after_host_prepare(filters, *args, **kwargs):
        prepared = original_prepare(filters, *args, **kwargs)
        if filters == filter_a:
            a_host_prepared.set()
            assert release_a.wait(timeout=5)
        return prepared

    index._prepare_host_filter = pause_a_after_host_prepare
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    with ThreadPoolExecutor(max_workers=1) as executor:
        delayed_a = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            filter_a,
            resolve,
            registrar,
            telemetry,
        )
        assert a_host_prepared.wait(timeout=5)
        assert index.search([0.0, 1.0], 1, filter_b, resolve, registrar)[0] == [2]
        release_a.set()
        assert delayed_a.result(timeout=5)[0] == [1]

    assert resolver_calls == {"a": 2, "b": 1}
    assert runtime.prepare_filter_words_count == 3
    assert telemetry.micro_batching_warm_fast_path is False
    assert telemetry.filter_cache_hit is False
    assert telemetry.filter_cache_eviction_fallback is True
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_stale_host_filter_uses_admitted_retry():
    class CountingGate:
        def __init__(self, gate):
            self.gate = gate
            self.acquire_count = 0
            self.lock = threading.Lock()

        def acquire(self, *args, **kwargs):
            with self.lock:
                self.acquire_count += 1
            return self.gate.acquire(*args, **kwargs)

        def release(self):
            return self.gate.release()

    runtime = FakeCuVSRuntime()
    index = make_micro_batch_index(runtime)
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    def resolve(_filters):
        return ([0b101], 2) if index.size == 3 else ([0b1101], 3)

    def registrar(_labels):
        return None

    assert index.search([1.0, 0.0], 1, filter_a, resolve, registrar)[0] == [1]
    stale = index._prepare_host_filter(filter_a, resolve, registrar)
    assert stale is not None
    index.upsert([delta(4, [2.0, 0.0], account_id="a")])

    original_prepare = index._prepare_host_filter
    prepare_calls = 0

    def return_stale_once(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 1:
            return stale
        return original_prepare(*args, **kwargs)

    index._prepare_host_filter = return_stale_once
    counting_gate = CountingGate(index._gpu_search_gate)
    index._gpu_search_gate = counting_gate
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    assert index.search(
        [1.0, 0.0],
        1,
        filter_a,
        resolve,
        registrar,
        telemetry,
    )[0] == [4]

    # One caller admission rejects the stale host result, the retry performs
    # the cold rebuild/materialization, and the worker owns the final search.
    assert counting_gate.acquire_count == 3
    assert prepare_calls == 2
    assert telemetry.micro_batching_warm_fast_path is False
    assert telemetry.records_generation == index._records_generation
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_singleton_deadline_and_exception_recovery():
    class FailingBatchRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.fail_next_batch = False

        def search_batch(self, index, queries, limit, mask):
            if self.fail_next_batch:
                self.fail_next_batch = False
                self.search_batch_sizes.append(len(queries))
                raise RuntimeError("injected batch failure")
            return super().search_batch(index, queries, limit, mask)

    runtime = FailingBatchRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_wait_ms=10.0,
    )
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    started = time.perf_counter()
    assert index.search([1.0, 0.0], 1, None, telemetry=telemetry)[0] == [1]
    assert time.perf_counter() - started < 1.0
    assert telemetry.batch_size == 1
    assert telemetry.batch_wait_ms >= 0.0

    runtime.search_batch_sizes.clear()
    runtime.fail_next_batch = True
    barrier = threading.Barrier(3)

    def failing_search(query):
        barrier.wait(timeout=5)
        return index.search(query, 1, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(failing_search, [1.0, 0.0]),
            executor.submit(failing_search, [0.0, 1.0]),
        ]
        barrier.wait(timeout=5)
        for future in futures:
            with pytest.raises(RuntimeError, match="injected batch failure"):
                future.result(timeout=5)

    assert runtime.search_batch_sizes == [2]
    assert index.search([1.0, 0.0], 1, None)[0] == [1]
    index.close()


def test_cuvs_micro_batch_worker_fatal_error_reaps_current_batch():
    class FatalBatchRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.fail_fatally = False

        def search_batch(self, index, queries, limit, mask):
            if self.fail_fatally:
                self.fail_fatally = False
                self.search_batch_sizes.append(len(queries))
                raise KeyboardInterrupt("injected worker failure")
            return super().search_batch(index, queries, limit, mask)

    runtime = FatalBatchRuntime()
    index = make_micro_batch_index(runtime)
    runtime.fail_fatally = True
    barrier = threading.Barrier(3)

    def fatal_search(query):
        barrier.wait(timeout=5)
        return index.search(query, 1, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fatal_search, [1.0, 0.0]),
            executor.submit(fatal_search, [0.0, 1.0]),
        ]
        barrier.wait(timeout=5)
        for future in futures:
            with pytest.raises(KeyboardInterrupt, match="injected worker failure"):
                future.result(timeout=5)

    assert runtime.search_batch_sizes == [2]
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    deadline = time.monotonic() + 5
    while index._micro_batch_worker_failure is None and time.monotonic() < deadline:
        time.sleep(0.001)
    assert index._micro_batch_worker_failure is not None
    index.close()


def test_cuvs_micro_batch_cancelled_warm_request_releases_pins_and_capacity():
    runtime = BlockingMicroBatchRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_batch_size=2,
        micro_batching_max_wait_ms=100.0,
    )
    runtime.block_next_batch = True

    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
        request = index._try_enqueue_warm_micro_batch(
            query=(0.0, 1.0),
            limit=1,
            filters=None,
            prepared_filter=None,
            telemetry=telemetry,
        )
        assert request is not None
        assert request.future.cancel()
        assert telemetry.micro_batching_warm_fast_path is True
        wait_for_warm_lookahead(index, 1)

        runtime.release_search.set()
        assert active.result(timeout=5)[0] == [1]

    deadline = time.monotonic() + 5
    while not request.released and time.monotonic() < deadline:
        time.sleep(0.001)
    assert request.released is True
    assert request.snapshot is None
    assert request.mask is None
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_cuvs_micro_batch_fatal_worker_releases_warm_lookahead_pins():
    class BlockingFatalRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__()
            self.block_and_fail = False
            self.search_started = threading.Event()
            self.fail_search = threading.Event()

        def search_batch(self, index, queries, limit, mask):
            if self.block_and_fail:
                self.block_and_fail = False
                self.search_started.set()
                assert self.fail_search.wait(timeout=5)
                raise KeyboardInterrupt("injected worker failure")
            return super().search_batch(index, queries, limit, mask)

    runtime = BlockingFatalRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_batch_size=2,
        micro_batching_max_wait_ms=100.0,
    )
    runtime.block_and_fail = True

    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(index.search, [1.0, 0.0], 1, None)
        assert runtime.search_started.wait(timeout=5)
        pending = [
            index._try_enqueue_warm_micro_batch(
                query=query,
                limit=1,
                filters=None,
                prepared_filter=None,
                telemetry=None,
            )
            for query in ((1.0, 0.0), (0.0, 1.0))
        ]
        assert all(request is not None for request in pending)
        wait_for_warm_lookahead(index, 2)
        runtime.fail_search.set()

        with pytest.raises(KeyboardInterrupt, match="injected worker failure"):
            active.result(timeout=5)
        for request in pending:
            assert request is not None
            with pytest.raises(KeyboardInterrupt, match="injected worker failure"):
                request.future.result(timeout=5)

    assert all(request.released for request in pending if request is not None)
    assert all(request.snapshot is None for request in pending if request is not None)
    assert all(request.mask is None for request in pending if request is not None)
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    assert index._micro_batch_worker_failure is not None
    index.close()


def test_cuvs_micro_batch_keeps_snapshot_generations_separate():
    runtime = FakeCuVSRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_wait_ms=100.0,
    )

    old_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    new_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        old_search = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            None,
            None,
            None,
            old_telemetry,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with index._micro_batch_condition:
                if index._micro_batch_pending:
                    break
            time.sleep(0.001)
        else:
            pytest.fail("old-snapshot search did not enter the micro-batch queue")

        index.upsert([delta(4, [2.0, 0.0], account_id="a")])
        new_search = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            None,
            None,
            None,
            new_telemetry,
        )
        assert old_search.result(timeout=5)[0] == [1]
        assert new_search.result(timeout=5)[0] == [4]

    assert runtime.search_batch_sizes == [1, 1]
    assert old_telemetry.micro_batching_warm_fast_path is True
    assert new_telemetry.micro_batching_warm_fast_path is False
    assert old_telemetry.records_generation < new_telemetry.records_generation
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    index.close()


def test_auto_native_route_does_not_report_micro_batching():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "micro_batching_enabled": True,
            "micro_batching_max_wait_ms": 1.0,
            "auto_filter_native_threshold": 10,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(1, [1.0, 0.0], account_id="a")])
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=True)

    with pytest.raises(CuVSNativeRouteError):
        index.search(
            [1.0, 0.0],
            1,
            {"op": "must", "field": "account_id", "conds": ["a"]},
            native_filter_resolver=lambda _filters: ([1], 1),
            native_filter_layout_registrar=lambda _labels: None,
            telemetry=telemetry,
        )

    assert telemetry.micro_batching_enabled is False
    assert telemetry.micro_batching_warm_fast_path is False
    assert telemetry.batch_size == 1
    assert runtime.search_batch_sizes == []
    index.close()


def test_cuvs_micro_batch_close_drains_queued_search_and_rejects_new_work():
    runtime = FakeCuVSRuntime()
    index = make_micro_batch_index(
        runtime,
        micro_batching_max_wait_ms=100.0,
    )
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        search_future = executor.submit(
            index.search,
            [1.0, 0.0],
            1,
            None,
            None,
            None,
            telemetry,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with index._micro_batch_condition:
                if index._micro_batch_pending:
                    break
            time.sleep(0.001)
        else:
            pytest.fail("search did not enter the micro-batch queue")
        close_future = executor.submit(index.close)
        assert search_future.result(timeout=5)[0] == [1]
        close_future.result(timeout=5)

    assert runtime.closed is True
    assert telemetry.micro_batching_warm_fast_path is True
    assert index._active_searches == 0
    assert index._micro_batch_warm_lookahead == 0
    with pytest.raises(RuntimeError, match="closed"):
        index.search([1.0, 0.0], 1, None)


@pytest.mark.parametrize(
    "config, message",
    [
        ({"micro_batching_max_batch_size": 0}, "between 1 and 8"),
        ({"micro_batching_max_batch_size": 9}, "between 1 and 8"),
        ({"micro_batching_max_wait_ms": -1}, "between 0 and 100"),
        ({"micro_batching_max_wait_ms": 100.1}, "between 0 and 100"),
        ({"micro_batching_max_wait_ms": float("inf")}, "between 0 and 100"),
        (
            {"micro_batching_enabled": True, "algorithm": "cagra"},
            "brute_force",
        ),
        (
            {"micro_batching_enabled": True, "max_concurrent_gpu_searches": 2},
            "max_concurrent_gpu_searches=1",
        ),
    ],
)
def test_cuvs_micro_batch_raw_config_validation(config, message):
    with pytest.raises(ValueError, match=message):
        CuVSDenseIndex(
            dimension=2,
            distance="ip",
            normalize_vectors=False,
            field_types={},
            config=config,
            runtime=FakeCuVSRuntime(),
        )


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


@pytest.mark.parametrize(
    ("evaluation", "expected_route"),
    [(([], 1), 1), (([0b11], 2), None)],
    ids=["narrow-native", "broad-gpu"],
)
def test_auto_mode_same_key_preflight_is_singleflight(evaluation, expected_route):
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
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.0, 1.0], account_id="a"),
        ]
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    start = threading.Barrier(4)
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    resolver_calls = 0

    def resolve(_filters):
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        assert release_resolver.wait(timeout=5)
        return evaluation

    def invoke():
        start.wait(timeout=5)
        return index.preflight_native_count(
            filter_a,
            resolve,
            lambda _labels: None,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(invoke) for _ in range(4)]
        assert resolver_started.wait(timeout=5)
        wait_for_preflight_participants(index, 4)
        release_resolver.set()
        routes = [future.result(timeout=5) for future in futures]

    assert routes == [expected_route] * 4
    assert resolver_calls == 1
    assert not index._preflight_flights


@pytest.mark.parametrize(
    "error_type",
    [RuntimeError, _PreflightAbort],
    ids=["exception", "base-exception"],
)
def test_auto_mode_same_key_preflight_broadcasts_error_and_allows_retry(error_type):
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
    start = threading.Barrier(4)
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    resolver_calls = 0

    def failing_resolve(_filters):
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        assert release_resolver.wait(timeout=5)
        raise error_type("preflight failed")

    def invoke():
        start.wait(timeout=5)
        return index.preflight_native_count(
            filter_a,
            failing_resolve,
            lambda _labels: None,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(invoke) for _ in range(4)]
        assert resolver_started.wait(timeout=5)
        wait_for_preflight_participants(index, 4)
        release_resolver.set()
        for future in futures:
            with pytest.raises(error_type, match="preflight failed"):
                future.result(timeout=5)

    assert resolver_calls == 1
    assert not index._preflight_flights
    assert (
        index.preflight_native_count(
            filter_a,
            lambda _filters: ([], 1),
            lambda _labels: None,
        )
        == 1
    )


def test_auto_mode_mutation_wakes_same_key_preflight_waiters_and_drops_old_result():
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
    start = threading.Barrier(2)
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    resolver_calls = 0

    def resolve(_filters):
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        assert release_resolver.wait(timeout=5)
        return [], 1

    def invoke():
        start.wait(timeout=5)
        return index.preflight_native_count(
            filter_a,
            resolve,
            lambda _labels: None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke) for _ in range(2)]
        assert resolver_started.wait(timeout=5)
        wait_for_preflight_participants(index, 2)
        index.add_candidates([candidate(20, [0.0, 1.0], account_id="a")])

        deadline = time.monotonic() + 5
        while not any(future.done() for future in futures) and time.monotonic() < deadline:
            time.sleep(0.001)
        assert any(future.done() for future in futures)

        release_resolver.set()
        assert [future.result(timeout=5) for future in futures] == [None, None]

    assert resolver_calls == 1
    assert not index._preflight_flights
    assert (
        index.preflight_native_count(
            filter_a,
            lambda _filters: ([0b11], 2),
            lambda _labels: None,
        )
        is None
    )


def test_auto_mode_close_wakes_same_key_preflight_waiters():
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
    start = threading.Barrier(2)
    resolver_started = threading.Event()
    release_resolver = threading.Event()

    def resolve(_filters):
        resolver_started.set()
        assert release_resolver.wait(timeout=5)
        return [], 1

    def invoke():
        start.wait(timeout=5)
        return index.preflight_native_count(
            filter_a,
            resolve,
            lambda _labels: None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke) for _ in range(2)]
        assert resolver_started.wait(timeout=5)
        wait_for_preflight_participants(index, 2)
        index.close()
        assert runtime.closed
        assert not index._preflight_flights
        release_resolver.set()
        assert [future.result(timeout=5) for future in futures] == [None, None]


def test_close_retries_runtime_cleanup_and_rejects_new_searches():
    runtime = FakeCuVSRuntime()
    runtime.close_error = RuntimeError("close failed")
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )

    with pytest.raises(RuntimeError, match="close failed"):
        index.close()
    assert not index._close_complete
    with pytest.raises(RuntimeError, match="dense index is closed"):
        index.search([1.0, 0.0], 1, None)

    index.close()
    index.close()
    assert index._close_complete
    assert runtime.closed
    assert runtime.close_count == 2


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


@pytest.mark.parametrize("words", [[], [0xFFFFFFFF]], ids=["empty", "short"])
def test_auto_mode_rejects_incomplete_gpu_filter_bitset(words):
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
    index.add_candidates([candidate(label, [1.0, 0.0], account_id="a") for label in range(33)])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    with pytest.raises(RuntimeError, match="incomplete bitset for GPU routing"):
        index.preflight_native_count(
            filter_a,
            lambda _filters: (words, 33),
            lambda _labels: None,
        )


def test_explicit_mode_rejects_short_gpu_bitset_below_auto_threshold():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"auto_filter_native_threshold": 100},
        runtime=runtime,
        auto_memory=False,
    )
    index.add_candidates([candidate(label, [1.0, 0.0], account_id="a") for label in range(33)])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    with pytest.raises(RuntimeError, match="incomplete bitset for GPU routing"):
        index.search(
            [1.0, 0.0],
            1,
            filter_a,
            lambda _filters: ([0b1], 1),
            lambda _labels: None,
        )


def test_auto_mode_accepts_omitted_bitset_for_native_route():
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
    index.add_candidates([candidate(label, [1.0, 0.0], account_id="a") for label in range(33)])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    assert (
        index.preflight_native_count(
            filter_a,
            lambda _filters: ([], 1),
            lambda _labels: None,
        )
        == 1
    )


def test_auto_mode_does_not_validate_stale_filter_projection_after_mutation():
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
    index.add_candidates([candidate(label, [1.0, 0.0], account_id="a") for label in range(32)])
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    def resolve_and_mutate(_filters):
        index.add_candidates([candidate(1000, [0.0, 1.0], account_id="a")])
        return [0xFFFFFFFF], 32

    assert (
        index.preflight_native_count(
            filter_a,
            resolve_and_mutate,
            lambda _labels: None,
        )
        is None
    )


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
