# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import gc
import threading
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)
from openviking.storage.vectordb.index import cuvs_index
from openviking.storage.vectordb.store.data import CandidateData
from openviking.storage.vectordb.store.local_store import StoreEngineProxy
from openviking.storage.vectordb.store.store_manager import StoreManager
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import bind_telemetry


class FakeCuVSRuntime:
    def __init__(self, metric):
        self.metric = metric
        self.search_count = 0
        self.search_batch_sizes = []

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

    def search_batch(self, index, queries, limit, mask):
        self.search_batch_sizes.append(len(queries))
        results = [self.search(index, query, limit, mask) for query in queries]
        return [result[0] for result in results], [result[1] for result in results]

    def close(self):
        pass


class BlockingFakeCuVSRuntime(FakeCuVSRuntime):
    def __init__(self, metric):
        super().__init__(metric)
        self.block_next_batch = False
        self.search_started = threading.Event()
        self.release_search = threading.Event()

    def search_batch(self, index, queries, limit, mask):
        if self.block_next_batch:
            self.block_next_batch = False
            self.search_started.set()
            assert self.release_search.wait(timeout=5)
        return super().search_batch(index, queries, limit, mask)


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


def test_local_collection_micro_batches_compatible_offset_and_projection_requests(monkeypatch):
    runtime = FakeCuVSRuntime("inner_product")
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_micro_batch_projection",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "account_id", "FieldType": "string"},
                {"FieldName": "rank", "FieldType": "int64"},
            ],
        },
        config={
            "dense_search": {
                "backend": "cuvs",
                "algorithm": "brute_force",
                "micro_batching_enabled": True,
                "micro_batching_max_batch_size": 2,
                "micro_batching_max_wait_ms": 50.0,
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
        collection.upsert_data(
            [
                {
                    "id": "first",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "account_id": "a",
                    "rank": 1,
                },
                {
                    "id": "second",
                    "vector": [0.8, 0.2, 0.0, 0.0],
                    "account_id": "b",
                    "rank": 2,
                },
                {
                    "id": "third",
                    "vector": [0.0, 1.0, 0.0, 0.0],
                    "account_id": "c",
                    "rank": 3,
                },
            ]
        )
        # Warm the immutable snapshot, then isolate the concurrent measurement.
        assert (
            collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
            .data[0]
            .id
            == "first"
        )
        runtime.search_batch_sizes.clear()
        barrier = threading.Barrier(3)

        def search_with_offset():
            barrier.wait(timeout=5)
            return collection.search_by_vector(
                "default",
                dense_vector=[1.0, 0.0, 0.0, 0.0],
                limit=1,
                offset=1,
                output_fields=["rank"],
            )

        def search_two():
            barrier.wait(timeout=5)
            return collection.search_by_vector(
                "default",
                dense_vector=[1.0, 0.0, 0.0, 0.0],
                limit=2,
                output_fields=["account_id"],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            offset_future = executor.submit(search_with_offset)
            two_future = executor.submit(search_two)
            barrier.wait(timeout=5)
            offset_result = offset_future.result(timeout=5)
            two_result = two_future.result(timeout=5)

        assert runtime.search_batch_sizes == [2]
        assert [(item.id, item.fields) for item in offset_result.data] == [("second", {"rank": 2})]
        assert [(item.id, item.fields) for item in two_result.data] == [
            ("first", {"account_id": "a"}),
            ("second", {"account_id": "b"}),
        ]
    finally:
        collection.close()


def test_local_collection_no_filter_requests_fill_warm_micro_batch(monkeypatch):
    runtime = BlockingFakeCuVSRuntime("inner_product")
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_micro_batch_no_filter",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "cuvs",
                "algorithm": "brute_force",
                "micro_batching_enabled": True,
                "micro_batching_max_batch_size": 2,
                "micro_batching_max_wait_ms": 100.0,
            }
        },
    )
    try:
        local_index = collection.create_index(
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
        assert (
            collection.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
            .data[0]
            .id
            == "first"
        )
        runtime.search_batch_sizes.clear()
        runtime.block_next_batch = True

        def search(vector):
            return collection.search_by_vector("default", dense_vector=vector, limit=1)

        with ThreadPoolExecutor(max_workers=3) as executor:
            active = executor.submit(search, [1.0, 0.0, 0.0, 0.0])
            assert runtime.search_started.wait(timeout=5)
            queued = [
                executor.submit(search, [0.0, 1.0, 0.0, 0.0]),
                executor.submit(search, [1.0, 0.0, 0.0, 0.0]),
            ]
            with local_index.dense_search._micro_batch_condition:
                assert local_index.dense_search._micro_batch_condition.wait_for(
                    lambda: local_index.dense_search._micro_batch_warm_lookahead == 2,
                    timeout=5,
                )
            assert all(not future.done() for future in queued)

            runtime.release_search.set()
            assert active.result(timeout=5).data[0].id == "first"
            assert [future.result(timeout=5).data[0].id for future in queued] == [
                "second",
                "first",
            ]

        assert runtime.search_batch_sizes == [1, 2]
        assert local_index.dense_search._micro_batch_warm_lookahead == 0
    finally:
        runtime.release_search.set()
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


def test_persistent_cuvs_recovery_deserializes_candidates_lazily(monkeypatch, tmp_path):
    patch_cuvs_runtime(monkeypatch)
    path = str(tmp_path / "cuvs-streaming-recovery")
    config = {"dense_search": {"backend": "cuvs", "algorithm": "brute_force"}}
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_streaming_recovery",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        path=path,
        config=config,
    )
    for index_name in ("first", "second"):
        collection.create_index(
            index_name,
            {
                "IndexName": index_name,
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
    record_count = 64
    collection.upsert_data(
        [
            {
                "id": f"row-{offset}",
                "vector": [float(offset + 1), 1.0, 0.0, 0.0],
            }
            for offset in range(record_count)
        ]
    )
    collection.close()

    original_from_bytes = CandidateData.from_bytes
    original_pack_vector = cuvs_index.CuVSDenseIndex._pack_vector
    candidate_refs = []
    events = []
    peak_live_candidates = 0

    def tracked_from_bytes(data):
        nonlocal peak_live_candidates
        candidate = original_from_bytes(data)
        candidate_refs.append(weakref.ref(candidate))
        events.append("decode")
        peak_live_candidates = max(
            peak_live_candidates,
            sum(candidate_ref() is not None for candidate_ref in candidate_refs),
        )
        return candidate

    def tracked_pack_vector(self, vector):
        events.append("pack")
        return original_pack_vector(self, vector)

    monkeypatch.setattr(CandidateData, "from_bytes", staticmethod(tracked_from_bytes))
    monkeypatch.setattr(cuvs_index.CuVSDenseIndex, "_pack_vector", tracked_pack_vector)

    reopened = get_or_create_local_collection(path=path, config=config)
    try:
        for index_name in ("first", "second"):
            index = reopened.get_index(index_name)
            assert index is not None
            assert index.dense_search is not None
            assert index.dense_search.size == record_count

        assert events.count("decode") == record_count * 2
        assert events.count("pack") == record_count * 2
        assert events.index("pack") < len(events) - 1 - events[::-1].index("decode")
        assert peak_live_candidates <= 3
        gc.collect()
        assert not any(candidate_ref() is not None for candidate_ref in candidate_refs)
    finally:
        reopened.close()


def test_persistent_native_recovery_does_not_scan_candidates(monkeypatch, tmp_path):
    path = str(tmp_path / "native-recovery-no-candidate-scan")
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "native_recovery_no_candidate_scan",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        path=path,
    )
    collection.create_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
        },
    )
    collection.upsert_data([{"id": "persisted", "vector": [1.0, 0.0, 0.0, 0.0]}])
    collection.close()

    original_read_all = StoreEngineProxy.read_all

    def reject_candidate_scan(self, table_name):
        if table_name == StoreManager.CandsTable:
            raise AssertionError("native snapshot recovery must not scan candidate rows")
        return original_read_all(self, table_name)

    monkeypatch.setattr(StoreEngineProxy, "read_all", reject_candidate_scan)
    reopened = get_or_create_local_collection(path=path)
    try:
        result = reopened.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["persisted"]
    finally:
        reopened.close()


def test_persistent_cuvs_recovery_materializes_when_native_snapshot_is_missing(
    monkeypatch, tmp_path
):
    patch_cuvs_runtime(monkeypatch)
    path = str(tmp_path / "cuvs-missing-native-snapshot")
    config = {"dense_search": {"backend": "cuvs", "algorithm": "brute_force"}}
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_missing_native_snapshot",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
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
        },
    )
    collection.upsert_data([{"id": "persisted", "vector": [1.0, 0.0, 0.0, 0.0]}])
    index = collection.get_index("default")
    assert index is not None
    version_dir = Path(index.version_dir)
    collection.close()

    markers = list(version_dir.glob("*.write_done"))
    assert markers
    for marker in markers:
        marker.unlink()

    reopened = get_or_create_local_collection(path=path, config=config)
    try:
        result = reopened.search_by_vector("default", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1)
        assert [item.id for item in result.data] == ["persisted"]
        recovered_index = reopened.get_index("default")
        assert recovered_index is not None
        assert recovered_index.dense_search is not None
        assert recovered_index.dense_search.size == 1
    finally:
        reopened.close()


def test_failed_cuvs_recovery_closes_candidate_iterator_and_partial_shadow(monkeypatch, tmp_path):
    patch_cuvs_runtime(monkeypatch)
    path = str(tmp_path / "cuvs-failed-streaming-recovery")
    config = {"dense_search": {"backend": "cuvs", "algorithm": "brute_force"}}
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "cuvs_failed_streaming_recovery",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
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
        },
    )
    collection.upsert_data([{"id": "persisted", "vector": [1.0, 0.0, 0.0, 0.0]}])
    collection.close()

    iterator_closed = False
    original_iter_all_cands_data = StoreManager.iter_all_cands_data

    def broken_candidates(_self):
        nonlocal iterator_closed
        try:
            yield CandidateData(label=1, vector=[1.0, 0.0, 0.0, 0.0])
            yield CandidateData(label=2, vector=[1.0, 0.0])
        finally:
            iterator_closed = True

    class TrackingRuntime(FakeCuVSRuntime):
        def __init__(self):
            super().__init__("inner_product")
            self.closed = False

        def close(self):
            self.closed = True

    runtime = TrackingRuntime()
    monkeypatch.setattr(StoreManager, "iter_all_cands_data", broken_candidates)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )

    with pytest.raises(ValueError, match="cuVS vector dimension mismatch"):
        get_or_create_local_collection(path=path, config=config)

    assert iterator_closed
    assert runtime.closed

    # Release must be complete when the constructor raises: restoring a valid
    # stream and runtime should allow this same persistent collection to reopen
    # immediately, without waiting for traceback GC to drop native locks.
    monkeypatch.setattr(StoreManager, "iter_all_cands_data", original_iter_all_cands_data)
    patch_cuvs_runtime(monkeypatch)
    reopened = get_or_create_local_collection(path=path, config=config)
    try:
        result = reopened.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )
        assert [item.id for item in result.data] == ["persisted"]
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


def test_auto_cuvs_background_memory_fallback_does_not_busy_loop(monkeypatch):
    class CountingMemoryRuntime(MemoryAwareFakeCuVSRuntime):
        def __init__(self):
            super().__init__("inner_product", free_memory_bytes=31)
            self.memory_info_count = 0
            self.memory_checked = threading.Event()

        def memory_info(self):
            self.memory_info_count += 1
            self.memory_checked.set()
            return super().memory_info()

    runtime = CountingMemoryRuntime()
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_memory_backoff",
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
        assert index.wait_for_background_rebuild(timeout=5)

        collection.upsert_data(
            [
                {"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]},
                {"id": "second", "vector": [0.0, 1.0, 0.0, 0.0]},
            ]
        )
        assert runtime.memory_checked.wait(timeout=5)
        assert index._dense_rebuild_completed.wait(timeout=5)
        with index._dense_rebuild_state_lock:
            assert index._dense_rebuild_memory_blocked
            index._dense_rebuild_memory_retry_not_before = float("inf")
        memory_checks_after_failure = runtime.memory_info_count

        assert not index.wait_for_background_rebuild(timeout=0.1)
        assert runtime.memory_info_count == memory_checks_after_failure
        assert runtime.build_count == 0

        runtime.free_memory_bytes = 32
        with index._dense_rebuild_state_lock:
            index._dense_rebuild_memory_retry_not_before = 0.0
        result = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )
        assert [item.id for item in result.data] == ["first"]
        assert index.wait_for_background_rebuild(timeout=5)
        result = collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )
        assert [item.id for item in result.data] == ["first"]
        assert runtime.build_count == 1
        assert runtime.search_count == 1
    finally:
        collection.close()


def test_auto_cuvs_bulk_ingest_defers_nested_rebuild_and_routes_native(monkeypatch):
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
            "CollectionName": "auto_cuvs_bulk_ingest",
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
                "auto_rebuild_debounce_ms": 100,
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
        assert index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 0

        collection.begin_bulk_ingest()
        try:
            collection.upsert_data([{"id": "first", "vector": [1.0, 0.0, 0.0, 0.0]}])
            # The scope remains open for longer than the normal debounce, but
            # no partial GPU index should be published.
            assert not runtime.build_started.wait(timeout=0.25)

            collection.begin_bulk_ingest()
            try:
                collection.upsert_data([{"id": "second", "vector": [0.0, 1.0, 0.0, 0.0]}])
                assert not runtime.build_started.wait(timeout=0.25)
            finally:
                collection.end_bulk_ingest()

            # Ending the inner scope must not resume rebuilds early. Dirty
            # reads stay correct by using the continuously updated native index.
            assert not runtime.build_started.wait(timeout=0.25)
            inner_collection = collection._Collection__collection
            inner_collection._rebuild_index("default", index)
            replacement_index = collection.get_index("default")
            assert replacement_index is not index
            assert index._dense_rebuild_thread is None
            assert replacement_index._dense_rebuild_thread is not None
            assert not runtime.build_started.wait(timeout=0.25)
            result = collection.search_by_vector(
                "default",
                dense_vector=[0.0, 1.0, 0.0, 0.0],
                limit=1,
            )
            assert [item.id for item in result.data] == ["second"]
            assert runtime.search_count == 0
        finally:
            collection.end_bulk_ingest()

        assert runtime.build_started.wait(timeout=1)
        assert replacement_index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 1
    finally:
        collection.close()


def test_index_install_keeps_post_publication_worker_failures_out_of_commit_result(monkeypatch):
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "replacement_worker_failure",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
    )
    inner_collection = collection._Collection__collection
    old_index = object()
    replacement_index = object()
    calls = []
    inner_collection.indexes.set("default", old_index)

    def failing_stop(index):
        calls.append(("stop", index))
        raise RuntimeError("injected stop failure")

    def tracked_start(index):
        calls.append(("start", index))

    monkeypatch.setattr(inner_collection, "_stop_index_background_rebuild", failing_stop)
    monkeypatch.setattr(inner_collection, "_start_index_background_rebuild", tracked_start)
    try:
        expected_generation = inner_collection._index_mutation_barrier.snapshot_generation()
        assert inner_collection._install_index(
            "default",
            replacement_index,
            expected_generation=expected_generation,
            expected_replaced=old_index,
            start_background_rebuild=True,
        )
        assert inner_collection.indexes.get("default") is replacement_index
        assert calls == [("stop", old_index), ("start", replacement_index)]
    finally:
        # Plain objects intentionally stand in for index lifecycle hooks above.
        inner_collection.indexes.clear()
        collection.close()


def test_auto_cuvs_stop_before_deferred_worker_start_is_durable(monkeypatch):
    runtime = MemoryAwareFakeCuVSRuntime("inner_product", free_memory_bytes=1 << 20)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_deferred_worker_retirement",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        },
        config={
            "dense_search": {
                "backend": "auto_cuvs",
                "algorithm": "brute_force",
                "auto_memory_reserve_mb": 0,
                "auto_memory_safety_factor": 1.0,
                "auto_background_rebuild": True,
            }
        },
    )
    inner_collection = collection._Collection__collection
    index = inner_collection._new_index(
        "default",
        {
            "IndexName": "default",
            "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
        },
        [],
        defer_dense_rebuild_start=True,
    )
    try:
        assert index._dense_rebuild_thread is None
        assert not index._dense_rebuild_stop.is_set()

        inner_collection._stop_index_background_rebuild(index)
        inner_collection._start_index_background_rebuild(index)

        assert index._dense_rebuild_stop.is_set()
        assert index._dense_rebuild_thread is None
        assert runtime.build_count == 0
    finally:
        index.close()
        collection.close()


def test_auto_cuvs_rebuild_discards_snapshot_stale_after_concurrent_write(monkeypatch):
    runtime = MemoryAwareFakeCuVSRuntime("inner_product", free_memory_bytes=1 << 20)
    monkeypatch.setattr(
        cuvs_index,
        "_CuVSRuntime",
        lambda _algorithm, _metric, _build_params, _search_params, _dtype: runtime,
    )
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "auto_cuvs_stale_collection_rebuild",
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
                "auto_rebuild_debounce_ms": 10,
            }
        },
    )
    rebuild_thread = None
    writer_thread = None
    allow_rebuild = threading.Event()
    allow_index_update = threading.Event()
    try:
        original_index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        assert original_index.wait_for_background_rebuild(timeout=5)

        collection.begin_bulk_ingest()
        try:
            collection.upsert_data([{"id": "before", "vector": [1.0, 0.0, 0.0, 0.0]}])
            inner_collection = collection._Collection__collection
            original_new_index = inner_collection._new_index
            original_install_index = inner_collection._install_index
            original_upsert_data = original_index.upsert_data
            snapshot_captured = threading.Event()
            store_committed = threading.Event()
            publication_attempted = threading.Event()
            writer_errors = []

            def delayed_new_index(*args, **kwargs):
                snapshot_captured.set()
                assert allow_rebuild.wait(timeout=5)
                return original_new_index(*args, **kwargs)

            def delayed_index_update(delta_list):
                store_committed.set()
                assert allow_index_update.wait(timeout=5)
                return original_upsert_data(delta_list)

            def tracked_install(*args, **kwargs):
                publication_attempted.set()
                return original_install_index(*args, **kwargs)

            def write_after_snapshot():
                try:
                    collection.upsert_data([{"id": "kept", "vector": [0.0, 1.0, 0.0, 0.0]}])
                except BaseException as exc:
                    writer_errors.append(exc)

            monkeypatch.setattr(inner_collection, "_new_index", delayed_new_index)
            monkeypatch.setattr(inner_collection, "_install_index", tracked_install)
            monkeypatch.setattr(original_index, "upsert_data", delayed_index_update)
            rebuild_thread = threading.Thread(
                target=inner_collection._rebuild_index,
                args=("default", original_index),
            )
            rebuild_thread.start()
            assert snapshot_captured.wait(timeout=5)

            # This batch commits after the replacement's Store snapshot. The
            # old index receives it; the stale replacement must not retire that
            # only searchable copy.
            writer_thread = threading.Thread(target=write_after_snapshot)
            writer_thread.start()
            assert store_committed.wait(timeout=5)
            allow_rebuild.set()
            assert publication_attempted.wait(timeout=5)
            assert rebuild_thread.is_alive()
            assert collection.get_index("default") is original_index

            allow_index_update.set()
            writer_thread.join(timeout=5)
            rebuild_thread.join(timeout=5)
            assert not writer_thread.is_alive()
            assert not rebuild_thread.is_alive()
            assert writer_errors == []
            assert collection.get_index("default") is original_index

            fetched = collection.fetch_data(["before", "kept"])
            assert [item.id for item in fetched.items] == ["before", "kept"]
            result = collection.search_by_vector(
                "default",
                dense_vector=[0.0, 1.0, 0.0, 0.0],
                limit=1,
            )
            assert [item.id for item in result.data] == ["kept"]
            assert runtime.search_count == 0
        finally:
            allow_rebuild.set()
            allow_index_update.set()
            if writer_thread is not None:
                writer_thread.join(timeout=5)
            if rebuild_thread is not None:
                rebuild_thread.join(timeout=5)
            collection.end_bulk_ingest()

        assert original_index.wait_for_background_rebuild(timeout=5)
        result = collection.search_by_vector(
            "default",
            dense_vector=[0.0, 1.0, 0.0, 0.0],
            limit=1,
        )
        assert [item.id for item in result.data] == ["kept"]
        assert runtime.search_count == 1
    finally:
        collection.close()


def test_auto_cuvs_bulk_ingest_delete_all_replacement_stays_suspended(monkeypatch):
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
            "CollectionName": "auto_cuvs_bulk_ingest_delete_all",
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
                "auto_rebuild_debounce_ms": 100,
            }
        },
    )
    try:
        original_index = collection.create_index(
            "default",
            {
                "IndexName": "default",
                "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
            },
        )
        assert original_index.wait_for_background_rebuild(timeout=5)

        collection.begin_bulk_ingest()
        try:
            collection.upsert_data([{"id": "removed", "vector": [1.0, 0.0, 0.0, 0.0]}])
            collection.delete_all_data()
            replacement_index = collection.get_index("default")
            assert replacement_index is not original_index

            collection.upsert_data([{"id": "kept", "vector": [0.0, 1.0, 0.0, 0.0]}])
            # delete_all_data replaces the index while the scope is active.
            # The replacement must inherit suspension for longer than debounce.
            assert not runtime.build_started.wait(timeout=0.25)
            result = collection.search_by_vector(
                "default",
                dense_vector=[0.0, 1.0, 0.0, 0.0],
                limit=1,
            )
            assert [item.id for item in result.data] == ["kept"]
            assert runtime.search_count == 0
        finally:
            collection.end_bulk_ingest()

        assert runtime.build_started.wait(timeout=1)
        assert replacement_index.wait_for_background_rebuild(timeout=5)
        assert runtime.build_count == 1
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
