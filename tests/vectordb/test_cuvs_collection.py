# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import multiprocessing
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import pytest

from openviking.storage.vectordb.collection.local_collection import (
    PersistCollection,
    get_or_create_local_collection,
)
from openviking.storage.vectordb.index import cuvs_index
from openviking.storage.vectordb.index.local_index import (
    IndexEngineProxy,
    LocalIndex,
    PersistentIndex,
)
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import bind_telemetry


class FakeCuVSRuntime:
    def __init__(self, metric):
        self.metric = metric
        self.search_count = 0

    def build(self, dataset):
        return [list(vector) for vector in dataset]

    def search(self, index, query, limit, mask):
        self.search_count += 1
        rows = []
        for offset, vector in enumerate(index):
            if mask is not None and not mask[offset]:
                continue
            if self.metric == "sqeuclidean":
                distance = sum(
                    (left - right) ** 2 for left, right in zip(query, vector, strict=True)
                )
                key = distance
            else:
                distance = sum(left * right for left, right in zip(query, vector, strict=True))
                key = -distance
            rows.append((key, offset, distance))
        rows.sort()
        rows = rows[:limit]
        return [row[1] for row in rows], [row[2] for row in rows]

    def close(self):
        pass


class MemoryAwareFakeCuVSRuntime(FakeCuVSRuntime):
    def __init__(self, metric, free_memory_bytes):
        super().__init__(metric)
        self.free_memory_bytes = free_memory_bytes
        self.build_count = 0

    def build(self, dataset):
        self.build_count += 1
        return super().build(dataset)

    def memory_info(self):
        return self.free_memory_bytes, 1 << 40

    def release_index(self):
        pass

    @staticmethod
    def is_out_of_memory(_exc):
        return False


def patch_cuvs_runtime(monkeypatch):
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda algorithm, metric, build_params, search_params, dtype: FakeCuVSRuntime(metric),
    )


def _delete_without_persisting_index(path, ready, hold):
    """Write a deletion delta, then wait to be terminated without closing."""

    try:
        collection = get_or_create_local_collection(path=path)
        collection.delete_data(["deleted"])
        ready.send(("ok", ""))
        hold.wait()
    except BaseException:
        ready.send(("error", traceback.format_exc()))
        raise


def test_local_collection_routes_dense_search_to_cuvs(monkeypatch):
    patch_cuvs_runtime(monkeypatch)
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_integration",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
                {"FieldName": "rank", "FieldType": "int64"},
                {"FieldName": "created_at", "FieldType": "date_time"},
                {"FieldName": "uri", "FieldType": "path"},
            ],
        },
        config={
            "dense_search": {
                "backend": "cuvs",
                "algorithm": "brute_force",
            }
        },
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["account_id", "rank", "created_at", "uri"],
            },
        )
        collection.upsert_data(
            [
                {
                    "id": "first",
                    "vector": [1, 0, 0, 0],
                    "account_id": "a",
                    "rank": 3,
                    "created_at": "2026-07-02T00:00:00Z",
                    "uri": "/docs/one",
                },
                {
                    "id": "second",
                    "vector": [0, 1, 0, 0],
                    "account_id": "a",
                    "rank": 2,
                    "created_at": "2026-07-01T00:00:00Z",
                    "uri": "/docs/deep/two",
                },
                {
                    "id": "hidden",
                    "vector": [1, 0, 0, 0],
                    "account_id": "b",
                    "rank": 1,
                    "created_at": "2026-06-01T00:00:00Z",
                    "uri": "/other/hidden",
                },
            ]
        )

        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=3,
            filters={"op": "must", "field": "account_id", "conds": ["a"]},
        )
        assert [item.id for item in result.data] == ["first", "second"]

        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=3,
            filters={
                "op": "must",
                "field": "uri",
                "conds": ["/docs"],
                "para": "-d=-1",
            },
        )
        assert [item.id for item in result.data] == ["first", "second"]

        # date_time conversion is deliberately delegated to the native engine.
        result = collection.search_by_vector(
            "default",
            dense_vector=[1, 0, 0, 0],
            limit=3,
            filters={
                "op": "and",
                "conds": [
                    {"op": "must", "field": "account_id", "conds": ["a"]},
                    {
                        "op": "range",
                        "field": "created_at",
                        "gte": "2026-07-02T00:00:00Z",
                    },
                ],
            },
        )
        assert [item.id for item in result.data] == ["first"]
        assert len(collection.search_by_scalar("default", "rank", limit=3).data) == 3

        collection.update_data(
            [
                {
                    "id": "second",
                    "vector": [2, 0, 0, 0],
                    "account_id": "a",
                    "rank": 2,
                }
            ]
        )
        collection.delete_data(["first"])
        result = collection.search_by_vector("default", dense_vector=[1, 0, 0, 0], limit=3)
        assert [item.id for item in result.data] == ["second", "hidden"]
    finally:
        collection.close()


def test_local_collection_records_cuvs_route_telemetry(monkeypatch):
    patch_cuvs_runtime(monkeypatch)
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_telemetry",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={"dense_search": {"backend": "cuvs", "algorithm": "brute_force"}},
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])
        telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)

        with bind_telemetry(telemetry):
            result = collection.search_by_vector(
                "default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1
            )

        assert [item.id for item in result.data] == ["first"]
        cuvs = telemetry.finish().summary["vector"]["cuvs"]
        assert cuvs["searches"] == 1
        assert cuvs["algorithms"] == {"brute_force": 1}
        assert cuvs["dtypes"] == {"float32": 1}
        assert cuvs["max_concurrent_gpu_searches"] == 1
        assert cuvs["routes"] == {"cuvs": 1}
        assert cuvs["filter_kinds"] == {"none": 1}
        assert cuvs["builds"] == 1
        assert cuvs["index_size_max"] == 1
    finally:
        collection.close()


def test_local_collection_allows_concurrent_warmed_cuvs_searches(monkeypatch):
    class ConcurrentFakeCuVSRuntime(FakeCuVSRuntime):
        def __init__(self, metric):
            super().__init__(metric)
            self.barrier = threading.Barrier(1)
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

    runtime = ConcurrentFakeCuVSRuntime("inner_product")
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_concurrent_search",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "cuvs",
                "algorithm": "brute_force",
                "max_concurrent_gpu_searches": 4,
            }
        },
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])
        assert (
            collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
            .data[0]
            .id
            == "first"
        )
        runtime.barrier = threading.Barrier(4)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(
                    collection.search_by_vector,
                    "default",
                    dense_vector=[1.0, 0.0, 0.0, 0.0],
                    limit=1,
                )
                for _ in range(4)
            ]
            assert [future.result(timeout=5).data[0].id for future in futures] == ["first"] * 4

        assert runtime.peak_active == 4
    finally:
        collection.close()


def test_persistent_collection_rehydrates_cuvs_from_local_store(monkeypatch, tmp_path):
    patch_cuvs_runtime(monkeypatch)
    path = str(tmp_path / "cuvs-persistent")
    config = {"dense_search": {"backend": "cuvs", "algorithm": "brute_force"}}
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_persistent",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
            ],
        },
        path=path,
        config=config,
    )
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            "ScalarIndex": ["account_id"],
        },
    )
    collection.upsert_data([{"id": "persisted", "vector": [1, 0, 0, 0], "account_id": "a"}])
    collection.close()

    reopened = get_or_create_local_collection(path=path, config=config)
    try:
        result = reopened.search_by_vector("default", dense_vector=[1, 0, 0, 0], limit=1)
        assert [item.id for item in result.data] == ["persisted"]
    finally:
        reopened.close()


def test_persistent_cuvs_rebuild_waits_for_deletion_replay(monkeypatch, tmp_path):
    path = str(tmp_path / "persistent-cuvs-recovery")
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "persistent_cuvs_recovery",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "kind", "FieldType": "string"},
            ],
        },
        path=path,
    )
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            "ScalarIndex": ["kind"],
        },
    )
    collection.upsert_data(
        [
            {
                "id": "deleted",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "kind": "deleted",
            },
            {
                "id": "survivor",
                "vector": [0.0, 1.0, 0.0, 0.0],
                "kind": "survivor",
            },
        ]
    )
    collection.close()

    # Commit only the store deletion. Terminating the process leaves the native
    # snapshot stale, so recovery has exactly one deletion to replay.
    ctx = multiprocessing.get_context("spawn")
    ready_parent, ready_child = ctx.Pipe(duplex=False)
    hold = ctx.Event()
    process = ctx.Process(
        target=_delete_without_persisting_index,
        args=(path, ready_child, hold),
    )
    process.start()
    ready_child.close()
    try:
        assert ready_parent.poll(20), "deletion worker did not finish"
        status, detail = ready_parent.recv()
        assert status == "ok", detail
    finally:
        ready_parent.close()
        if process.is_alive():
            process.terminate()
        process.join(10)
        assert not process.is_alive()

    runtime = MemoryAwareFakeCuVSRuntime("inner_product", free_memory_bytes=1 << 30)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )

    replay_completed = threading.Event()
    layout_registered = threading.Event()
    layouts_before_replay = []
    replay_operations = []
    original_init = PersistentIndex.__init__
    original_replay = PersistCollection._replay_recovery_records
    original_set_filter_layout = IndexEngineProxy.set_filter_layout

    def synchronize_constructor(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # The old lifecycle starts the worker during construction. Waiting here
        # forces its stale layout to commit before recovery can replay deletion.
        if self._dense_rebuild_thread is not None:
            assert layout_registered.wait(timeout=5)

    def observe_replay(self, *, index_name, index, records, operation):
        replay_operations.append((operation, len(records)))
        result = original_replay(
            self,
            index_name=index_name,
            index=index,
            records=records,
            operation=operation,
        )
        replay_completed.set()
        return result

    def observe_filter_layout(self, ordered_labels):
        result = original_set_filter_layout(self, ordered_labels)
        if not replay_completed.is_set():
            layouts_before_replay.append(tuple(ordered_labels))
        layout_registered.set()
        return result

    monkeypatch.setattr(PersistentIndex, "__init__", synchronize_constructor)
    monkeypatch.setattr(PersistCollection, "_replay_recovery_records", observe_replay)
    monkeypatch.setattr(IndexEngineProxy, "set_filter_layout", observe_filter_layout)

    reopened = get_or_create_local_collection(
        path=path,
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        assert replay_operations == [("delete", 1)]
        assert replay_completed.is_set()
        index = reopened.get_index("default")
        assert index is not None
        assert index.wait_for_background_rebuild(timeout=5)
        assert layout_registered.wait(timeout=5)
        assert layouts_before_replay == []
        assert runtime.build_count == 1

        unfiltered = reopened.search_by_vector(
            "default", dense_vector=[0.0, 1.0, 0.0, 0.0], limit=2
        )
        filtered = reopened.search_by_vector(
            "default",
            dense_vector=[0.0, 1.0, 0.0, 0.0],
            limit=2,
            filters={"op": "must", "field": "kind", "conds": ["survivor"]},
        )
        deleted = reopened.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=2,
            filters={"op": "must", "field": "kind", "conds": ["deleted"]},
        )
        assert [item.id for item in unfiltered.data] == ["survivor"]
        assert [item.id for item in filtered.data] == ["survivor"]
        assert deleted.data == []
    finally:
        reopened.close()


def test_auto_cuvs_falls_back_then_retries_when_memory_is_available(monkeypatch):
    runtimes = []

    def make_runtime(_algorithm, metric, _build_params, _search_params, _dtype):
        runtime = MemoryAwareFakeCuVSRuntime(metric, free_memory_bytes=31)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(cuvs_index, "_CuVSRuntime", make_runtime)
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_integration",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
            }
        },
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["account_id"],
            },
        )
        collection.upsert_data(
            [
                {
                    "id": "first",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "account_id": "a",
                },
                {
                    "id": "second",
                    "vector": [0.0, 1.0, 0.0, 0.0],
                    "account_id": "b",
                },
            ]
        )

        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert runtimes[0].build_count == 0

        runtimes[0].free_memory_bytes = 32
        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert runtimes[0].build_count == 1

        result = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
            filters={"op": "must", "field": "account_id", "conds": ["a"]},
        )
        assert [item.id for item in result.data] == ["first"]
        # The selective filtered query uses native search in auto mode.
        assert runtimes[0].search_count == 1
    finally:
        collection.close()


def test_auto_cuvs_background_rebuild_warms_gpu_before_query(monkeypatch):
    runtime = MemoryAwareFakeCuVSRuntime("inner_product", free_memory_bytes=64)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_background",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data(
            [
                {"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]},
                {"id": "second", "vector": [0.0, 1.0, 0.0, 0.0]},
            ]
        )

        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 1
        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert runtime.search_count == 1
    finally:
        collection.close()


def test_auto_cuvs_initial_rebuild_waits_for_native_data(monkeypatch):
    runtime = MemoryAwareFakeCuVSRuntime("inner_product", free_memory_bytes=1 << 30)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )

    native_add_done = threading.Event()
    layout_registered = threading.Event()
    original_add_data = IndexEngineProxy.add_data
    original_set_filter_layout = IndexEngineProxy.set_filter_layout
    original_schedule = LocalIndex._schedule_dense_rebuild

    def observe_native_add(self, candidates):
        result = original_add_data(self, candidates)
        native_add_done.set()
        return result

    def observe_filter_layout(self, ordered_labels):
        result = original_set_filter_layout(self, ordered_labels)
        layout_registered.set()
        return result

    def wait_for_early_rebuild(self):
        original_schedule(self)
        # Force the buggy pre-add schedule to wait until its worker publishes
        # the stale layout; the fixed lifecycle never enters this branch.
        if not native_add_done.is_set():
            assert layout_registered.wait(timeout=5)

    monkeypatch.setattr(IndexEngineProxy, "add_data", observe_native_add)
    monkeypatch.setattr(IndexEngineProxy, "set_filter_layout", observe_filter_layout)
    monkeypatch.setattr(LocalIndex, "_schedule_dense_rebuild", wait_for_early_rebuild)

    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_initial_rebuild",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "kind", "FieldType": "string"},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0], "kind": "x"}])
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["kind"],
            },
        )

        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 1
        unfiltered = collection.search_by_vector(
            "default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1
        )
        filtered = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
            filters={"op": "must", "field": "kind", "conds": ["x"]},
        )
        assert [item.id for item in unfiltered.data] == ["first"]
        assert [item.id for item in filtered.data] == ["first"]
    finally:
        collection.close()


def test_auto_cuvs_background_rebuild_coalesces_mutations_and_routes_native(
    monkeypatch,
):
    class BlockingBuildRuntime(MemoryAwareFakeCuVSRuntime):
        def __init__(self):
            super().__init__("inner_product", free_memory_bytes=1 << 20)
            self.build_started = threading.Event()
            self.resume_build = threading.Event()

        def build(self, dataset):
            self.build_started.set()
            assert self.resume_build.wait(timeout=5)
            return super().build(dataset)

    runtime = BlockingBuildRuntime()
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_coalesced_rebuild",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])
        assert runtime.build_started.wait(timeout=5)

        # The build does not hold the cross-backend lock. Queries remain
        # correct through native search and writes coalesce into one follow-up.
        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert runtime.search_count == 0
        collection.update_data([{"id": "first", "vector": [0.0, 1.0, 0.0, 0.0]}])
        collection.update_data([{"id": "first", "vector": [0.0, 0.0, 1.0, 0.0]}])

        runtime.resume_build.set()
        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 2
    finally:
        runtime.resume_build.set()
        collection.close()


def test_auto_cuvs_background_debounce_is_not_extended_by_dirty_reads(monkeypatch):
    class BuildStartedRuntime(MemoryAwareFakeCuVSRuntime):
        def __init__(self):
            super().__init__("inner_product", free_memory_bytes=1 << 20)
            self.build_started = threading.Event()

        def build(self, dataset):
            self.build_started.set()
            return super().build(dataset)

    runtime = BuildStartedRuntime()
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_read_safe_debounce",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 200,
            }
        },
    )
    stop_reads = threading.Event()
    reader_started = threading.Event()

    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        assert index.wait_for_background_rebuild(timeout=5)

        def keep_reading():
            reader_started.set()
            while not stop_reads.wait(0.005):
                collection.search_by_vector(
                    "default",
                    dense_vector=[1.0, 0.0, 0.0, 0.0],
                    limit=1,
                )

        reader = threading.Thread(target=keep_reading)
        reader.start()
        assert reader_started.wait(timeout=1)
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])

        # Reads continue throughout the non-zero trailing-edge mutation debounce.
        # They must not keep pushing its deadline out indefinitely.
        assert runtime.build_started.wait(timeout=1)
        assert reader.is_alive()
        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 1
    finally:
        stop_reads.set()
        if "reader" in locals():
            reader.join(timeout=5)
        collection.close()


def test_auto_cuvs_clean_to_dirty_race_never_rebuilds_on_query_thread(monkeypatch):
    class RaceBuildRuntime(MemoryAwareFakeCuVSRuntime):
        def __init__(self):
            super().__init__("inner_product", free_memory_bytes=1 << 20)
            self.build_attempts = 0
            self.block_build = False
            self.build_started = threading.Event()
            self.resume_build = threading.Event()

        def build(self, dataset):
            self.build_attempts += 1
            if self.block_build:
                self.build_started.set()
                assert self.resume_build.wait(timeout=5)
            return super().build(dataset)

    runtime = RaceBuildRuntime()
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_clean_dirty_race",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])
        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_attempts == 1

        original_search_cuvs = index._search_cuvs
        mutation_injected = threading.Event()
        runtime.block_build = True

        def inject_mutation_after_clean_check(*args, **kwargs):
            if not mutation_injected.is_set():
                mutation_injected.set()
                collection.update_data([{"id": "first", "vector": [0.0, 1.0, 0.0, 0.0]}])
                assert runtime.build_started.wait(timeout=5)
            return original_search_cuvs(*args, **kwargs)

        monkeypatch.setattr(index, "_search_cuvs", inject_mutation_after_clean_check)
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                collection.search_by_vector,
                "default",
                dense_vector=[0.0, 1.0, 0.0, 0.0],
                limit=1,
            )
            # The request routes to the current native index even while the sole
            # rebuild for this generation is blocked in the background worker.
            assert [item.id for item in future.result(timeout=1).data] == ["first"]
        finally:
            runtime.resume_build.set()
            executor.shutdown(wait=True)

        assert runtime.build_attempts == 2
        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_attempts == 2
    finally:
        runtime.resume_build.set()
        collection.close()


def test_auto_cuvs_background_failure_surfaces_to_wait_and_query(monkeypatch):
    class FailingBuildRuntime(MemoryAwareFakeCuVSRuntime):
        def __init__(self):
            super().__init__("inner_product", free_memory_bytes=1 << 20)
            self.fail_build = True

        def build(self, dataset):
            if self.fail_build:
                raise RuntimeError("injected background build failure")
            return super().build(dataset)

    runtime = FailingBuildRuntime()
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_background_failure",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])

        with pytest.raises(RuntimeError, match="injected background build failure"):
            index.wait_for_background_rebuild(timeout=5)
        with pytest.raises(RuntimeError, match="injected background build failure"):
            collection.search_by_vector(
                "default",
                dense_vector=[1.0, 0.0, 0.0, 0.0],
                limit=1,
            )

        # A later mutation is a new rebuild generation and clears the stale
        # failure, allowing a transient runtime problem to recover.
        runtime.fail_build = False
        collection.update_data([{"id": "first", "vector": [0.0, 1.0, 0.0, 0.0]}])
        assert index.wait_for_background_rebuild(timeout=5)
        assert (
            collection.search_by_vector(
                "default",
                dense_vector=[0.0, 1.0, 0.0, 0.0],
                limit=1,
            )
            .data[0]
            .id
            == "first"
        )
    finally:
        collection.close()


def test_auto_cuvs_background_memory_fallback_retries_on_later_query(monkeypatch):
    runtime = MemoryAwareFakeCuVSRuntime("inner_product", free_memory_bytes=31)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_background_memory_retry",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 0,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
                "auto_rebuild_debounce_ms": 0,
            }
        },
    )
    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        collection.upsert_data(
            [
                {"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]},
                {"id": "second", "vector": [0.0, 1.0, 0.0, 0.0]},
            ]
        )
        assert index._dense_rebuild_completed.wait(timeout=5)
        assert index.dense_search.needs_rebuild
        assert runtime.build_count == 0

        runtime.free_memory_bytes = 32
        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 1

        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert runtime.search_count == 1
    finally:
        collection.close()


def test_auto_cuvs_selective_first_query_skips_gpu_build(monkeypatch):
    runtimes = []
    dense_search_calls = 0

    original_search = cuvs_index.CuVSDenseIndex.search

    def tracked_search(self, *args, **kwargs):
        nonlocal dense_search_calls
        dense_search_calls += 1
        return original_search(self, *args, **kwargs)

    def make_runtime(_algorithm, metric, _build_params, _search_params, _dtype):
        runtime = MemoryAwareFakeCuVSRuntime(metric, free_memory_bytes=64)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(cuvs_index, "_CuVSRuntime", make_runtime)
    monkeypatch.setattr(cuvs_index.CuVSDenseIndex, "search", tracked_search)
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_selective_first",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "filter_cache_size": 1,
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_filter_native_threshold": 1,
            }
        },
    )
    try:
        collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
                "ScalarIndex": ["account_id"],
            },
        )
        collection.upsert_data(
            [
                {
                    "id": "first",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "account_id": "a",
                },
                {
                    "id": "second",
                    "vector": [0.0, 1.0, 0.0, 0.0],
                    "account_id": "b",
                },
            ]
        )

        telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
        with bind_telemetry(telemetry):
            result = collection.search_by_vector(
                "default",
                dense_vector=[1.0, 0.0, 0.0, 0.0],
                limit=1,
                filters={"op": "must", "field": "account_id", "conds": ["a"]},
            )
        assert [item.id for item in result.data] == ["first"]
        cuvs = telemetry.finish().summary["vector"]["cuvs"]
        assert cuvs["routes"] == {"native_filter_threshold": 1}
        assert cuvs["native_filter_reuses"] == 1
        assert dense_search_calls == 0
        assert runtimes[0].build_count == 0
        assert runtimes[0].search_count == 0

        result = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
            filters={"op": "must", "field": "account_id", "conds": ["a"]},
        )
        assert [item.id for item in result.data] == ["first"]
        assert dense_search_calls == 0

        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["first"]
        assert dense_search_calls == 1
        assert runtimes[0].build_count == 1
        assert runtimes[0].search_count == 1
    finally:
        collection.close()


def test_auto_cuvs_keeps_native_when_runtime_is_unavailable(monkeypatch):
    def unavailable_runtime(*_args, **_kwargs):
        raise cuvs_index.CuVSUnavailableError("unavailable for test")

    monkeypatch.setattr(cuvs_index, "_CuVSRuntime", unavailable_runtime)
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_unavailable",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={"dense_search": {"backend": "auto_cuvs"}},
    )
    try:
        index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        assert index.dense_search is None

        collection.upsert_data([{"id": "native", "vector": [1.0, 0.0, 0.0, 0.0]}])
        result = collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["native"]
    finally:
        collection.close()
