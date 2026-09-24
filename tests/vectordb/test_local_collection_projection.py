# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace

import pytest

from openviking.storage.vectordb.collection.local_collection import LocalCollection
from openviking.storage.vectordb.store.data import CandidateData
from openviking.storage.vectordb.store.store_manager import StoreManager


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


def test_search_by_vector_projects_fields_without_decoding_vectors():
    store = _FakeStoreManager(
        [
            _candidate(11, "first", "/docs/one", [1.0, 0.0]),
            _candidate(12, "second", "/docs/two", [0.0, 1.0]),
        ]
    )

    result = _collection(store).search_by_vector(
        "default", dense_vector=[1.0, 0.0], output_fields=["uri"]
    )

    assert store.calls == [("fields", [11, 12])]
    assert [(item.id, item.fields, item.score) for item in result.data] == [
        ("first", {"uri": "/docs/one"}, 0.9),
        ("second", {"uri": "/docs/two"}, 0.8),
    ]


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
