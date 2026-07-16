# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)
from openviking.storage.vectordb.index import cuvs_index
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
