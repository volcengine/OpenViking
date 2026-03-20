# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from openviking.storage.vectordb.collection.result import AggregateResult, SearchResult
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@dataclass
class _RecordingCollection:
    search_calls: list[dict[str, Any]] = field(default_factory=list)
    aggregate_calls: list[dict[str, Any]] = field(default_factory=list)
    meta_data: Dict[str, Any] = field(default_factory=dict)

    def search_by_vector(self, **kwargs: Any) -> SearchResult:
        self.search_calls.append({"kind": "vector", **kwargs})
        return SearchResult()

    def search_by_scalar(self, **kwargs: Any) -> SearchResult:
        self.search_calls.append({"kind": "scalar", **kwargs})
        return SearchResult()

    def search_by_random(self, **kwargs: Any) -> SearchResult:
        self.search_calls.append({"kind": "random", **kwargs})
        return SearchResult()

    def aggregate_data(self, **kwargs: Any) -> AggregateResult:
        self.aggregate_calls.append(kwargs)
        return AggregateResult(agg={"_total": 0})

    def get_meta_data(self) -> Dict[str, Any]:
        return self.meta_data


class _RecordingAdapter(CollectionAdapter):
    mode = "local"

    def __init__(self, collection_name: str, index_name: str):
        super().__init__(collection_name=collection_name, index_name=index_name)
        self._collection = _RecordingCollection()

    @classmethod
    def from_config(cls, config: Any) -> "_RecordingAdapter":
        raise NotImplementedError

    def _load_existing_collection_if_needed(self) -> None:
        return None

    def _create_backend_collection(self, meta: Dict[str, Any]):
        raise NotImplementedError


@dataclass
class _FakeBackendCollection:
    meta_data: Dict[str, Any] = field(default_factory=dict)

    def get_meta_data(self) -> Dict[str, Any]:
        return self.meta_data


class _FakeCreateCollectionAdapter:
    mode = "local"

    def __init__(self):
        self.create_collection_calls: list[dict[str, Any]] = []
        self._collection = _FakeBackendCollection(meta_data={"Fields": []})

    def create_collection(self, **kwargs: Any) -> bool:
        self.create_collection_calls.append(kwargs)
        return True

    def get_collection(self) -> _FakeBackendCollection:
        return self._collection


def test_collection_adapter_query_and_count_use_configured_index_name():
    adapter = _RecordingAdapter(collection_name="context", index_name="context_idx")

    adapter.query(query_vector=[0.1, 0.2])
    adapter.query(order_by="updated_at", order_desc=True)
    adapter.query()
    adapter.count()

    assert [call["index_name"] for call in adapter._collection.search_calls] == [
        "context_idx",
        "context_idx",
        "context_idx",
    ]
    assert adapter._collection.aggregate_calls == [
        {"index_name": "context_idx", "op": "count", "filters": {}}
    ]


def test_viking_vector_index_backend_create_collection_uses_configured_index_name(monkeypatch):
    adapter = _FakeCreateCollectionAdapter()
    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.create_collection_adapter",
        lambda config: adapter,
    )

    backend = VikingVectorIndexBackend(
        VectorDBBackendConfig(
            backend="local",
            path="/tmp/ov-test-index-name",
            name="context",
            index_name="context_idx",
            dimension=8,
        )
    )

    import asyncio

    created = asyncio.get_event_loop().run_until_complete(
        backend.create_collection(
            "context",
            {
                "CollectionName": "context",
                "Fields": [{"FieldName": "vector", "FieldType": "vector", "Dim": 8}],
            },
        )
    )

    assert created is True
    assert adapter.create_collection_calls == [
        {
            "name": "context",
            "schema": {
                "CollectionName": "context",
                "Fields": [{"FieldName": "vector", "FieldType": "vector", "Dim": 8}],
            },
            "distance": "cosine",
            "sparse_weight": 0.0,
            "index_name": "context_idx",
        }
    ]
