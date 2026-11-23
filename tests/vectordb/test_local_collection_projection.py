# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.vectordb.collection.local_collection import LocalCollection
from openviking.storage.vectordb.store.data import CandidateData
from openviking.storage.vectordb.store.store_manager import StoreManager
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter


class _FakeIndex:
    def __init__(self, labels, scores):
        self.labels = labels
        self.scores = scores

    def search(self, *_args, **_kwargs):
        return list(self.labels), list(self.scores)


class _FakeIndexes:
    def __init__(self, index):
        self.index = index

    def get(self, _name):
        return self.index


class _FakeStoreManager:
    def __init__(self, candidates, fields_payloads=None):
        self.candidates = candidates
        self.fields_payloads = fields_payloads
        self.calls = []

    def fetch_cands_data(self, labels):
        self.calls.append(("data", list(labels)))
        return list(self.candidates)

    def fetch_cands_fields(self, labels):
        self.calls.append(("fields", list(labels)))
        if self.fields_payloads is not None:
            return list(self.fields_payloads)
        return [
            candidate.fields if candidate is not None else None for candidate in self.candidates
        ]


def _candidate(label, doc_id, uri, vector):
    return CandidateData(
        label=label,
        vector=vector,
        fields=json.dumps({"doc_id": doc_id, "uri": uri}),
    )


def _collection(store, labels=(11, 12), scores=(0.9, 0.8)):
    collection = object.__new__(LocalCollection)
    collection.indexes = _FakeIndexes(_FakeIndex(labels, scores))
    collection.store_mgr = store
    collection.meta = SimpleNamespace(
        primary_key="doc_id",
        vector_key="embedding",
        fields_dict={"doc_id": {}, "embedding": {}, "uri": {}},
    )
    return collection


def test_search_by_vector_projects_only_final_decayed_results(tmp_path, monkeypatch):
    adapter = LocalCollectionAdapter("context", str(tmp_path), "default")
    try:
        adapter.create_collection(
            "context",
            CollectionSchemas.context_collection("context", 4),
            distance="cosine",
            sparse_weight=0,
            index_name="default",
        )
        adapter.upsert(
            [
                {
                    "id": str(index),
                    "uri": f"viking://resources/{index}",
                    "vector": [similarity, (1 - similarity**2) ** 0.5, 0, 0],
                    **(
                        {
                            "updated_at": "2026-01-01T00:00:00Z"
                            if index < 3
                            else "2026-01-09T00:00:00Z"
                        }
                        if index < 5
                        else {}
                    ),
                }
                for index, similarity in enumerate([1.0, 0.9, 0.8, 0.7, 0.6, 0.5])
            ]
        )
        adapter.close()
        # Reloading must retain the timestamp index used for scoring.
        original_scores = {
            item["id"]: item["_score"] for item in adapter.query(query_vector=[1, 0, 0, 0], limit=6)
        }
        local = adapter.get_collection()._Collection__collection
        fetch = Mock(wraps=local.store_mgr.fetch_cands_fields)
        monkeypatch.setattr(local.store_mgr, "fetch_cands_fields", fetch)
        original_full_fetch = local.store_mgr.fetch_cands_data
        full_decode = Mock(side_effect=AssertionError("projected results must not decode vectors"))
        monkeypatch.setattr(local.store_mgr, "fetch_cands_data", full_decode)
        options = {
            "query_vector": [1, 0, 0, 0],
            "output_fields": ["uri"],
            "advance": {"time_decay": {"protection": "2d", "origin": "2026-01-10T00:00:00Z"}},
        }
        result = adapter.query(**options, limit=2)
        assert [item["id"] for item in result] == ["3", "4"]
        assert [item["_score"] for item in result] == pytest.approx(
            [original_scores["3"], original_scores["4"]]
        )
        assert [item["_time_score"] for item in result] == [1.0, 1.0]
        assert [item["uri"] for item in result] == ["viking://resources/3", "viking://resources/4"]
        assert all("vector" not in item and "updated_at" not in item for item in result)
        assert sum(len(call.args[0]) for call in fetch.call_args_list) == 2

        # Top-1 considers only three vector candidates, not the whole collection.
        one = adapter.query(**options, limit=1)
        assert one[0]["id"] == "0"
        assert one[0]["_origin_score"] == pytest.approx(original_scores["0"])
        assert one[0]["_score"] == pytest.approx(original_scores["0"] * 0.5)
        page = adapter.query(**options, limit=2, offset=1)
        assert [item["id"] for item in page] == ["4", "5"]
        assert page[1]["_score"] == pytest.approx(original_scores["5"])
        assert "_time_score" not in page[1]

        monkeypatch.setattr(local.store_mgr, "fetch_cands_data", original_full_fetch)
        adapter.update_data([{"id": "0", "updated_at": "2026-01-10T00:00:00Z"}])
        refreshed = adapter.query(**options, limit=2)
        assert refreshed[0]["id"] == "0"
        assert refreshed[0]["_score"] == pytest.approx(original_scores["0"])
    finally:
        adapter.close()


@pytest.mark.parametrize("output_fields", [None, [], ["uri", "embedding"]])
def test_search_by_vector_preserves_full_and_explicit_vector_hydration(output_fields):
    store = _FakeStoreManager(
        [
            _candidate(11, "first", "/docs/one", [1.0, 0.0]),
            _candidate(12, "second", "/docs/two", [0.0, 1.0]),
        ]
    )

    result = _collection(store).search_by_vector(
        "default", dense_vector=[1.0, 0.0], output_fields=output_fields
    )

    assert store.calls == [("data", [11, 12])]
    assert [item.id for item in result.data] == ["first", "second"]
    assert [item.fields["embedding"] for item in result.data] == [
        [1.0, 0.0],
        [0.0, 1.0],
    ]
    if output_fields:
        assert result.data[0].fields == {
            "uri": "/docs/one",
            "embedding": [1.0, 0.0],
        }
    else:
        assert result.data[0].fields == {
            "doc_id": "first",
            "embedding": [1.0, 0.0],
            "uri": "/docs/one",
        }


def test_search_by_vector_supports_vector_only_projection():
    store = _FakeStoreManager(
        [
            _candidate(11, "first", "/docs/one", [1.0, 0.0]),
            _candidate(12, "second", "/docs/two", [0.0, 1.0]),
        ]
    )

    result = _collection(store).search_by_vector(
        "default",
        dense_vector=[1.0, 0.0],
        output_fields=["embedding"],
    )

    assert store.calls == [("data", [11, 12])]
    assert [(item.id, item.fields) for item in result.data] == [
        ("first", {"embedding": [1.0, 0.0]}),
        ("second", {"embedding": [0.0, 1.0]}),
    ]


def test_projected_hydration_skips_missing_and_corrupt_rows_without_misalignment():
    store = _FakeStoreManager(
        candidates=[],
        fields_payloads=[
            json.dumps({"doc_id": "first", "uri": "/docs/one"}),
            None,
            '{"doc_id": "broken"',
            "null",
            json.dumps({"doc_id": "fourth", "uri": "/docs/four"}),
        ],
    )
    collection = _collection(
        store,
        labels=(11, 12, 13, 14, 15),
        scores=(0.9, 0.8, 0.7, 0.65, 0.6),
    )

    result = collection.search_by_vector("default", dense_vector=[1.0, 0.0], output_fields=["uri"])

    assert [(item.id, item.fields, item.score) for item in result.data] == [
        ("first", {"uri": "/docs/one"}, 0.9),
        ("fourth", {"uri": "/docs/four"}, 0.6),
    ]


def test_store_manager_projects_fields_and_preserves_missing_positions(monkeypatch):
    payloads = {
        "11": _candidate(11, "first", "/docs/one", [1.0, 0.0]).serialize(),
        "13": _candidate(13, "third", "/docs/three", [0.0, 1.0]).serialize(),
    }

    class _Storage:
        def read(self, keys, table):
            assert table == StoreManager.CandsTable
            return [payloads.get(key, b"") for key in keys]

    manager = StoreManager(_Storage())

    def reject_full_decode(_payload):
        raise AssertionError("projected fetch must not deserialize CandidateData")

    monkeypatch.setattr(CandidateData, "from_bytes", staticmethod(reject_full_decode))

    fields = manager.fetch_cands_fields([11, 12, 13, 11])

    assert fields == [
        json.dumps({"doc_id": "first", "uri": "/docs/one"}),
        None,
        json.dumps({"doc_id": "third", "uri": "/docs/three"}),
        json.dumps({"doc_id": "first", "uri": "/docs/one"}),
    ]
