# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Metadata updates must fail loudly when the metadata file cannot be written."""

import json

import pytest

from openviking.storage.vectordb.meta.collection_meta import create_collection_meta
from openviking.storage.vectordb.meta.index_meta import create_index_meta
from openviking.storage.vectordb.meta.local_dict import PersistentDict
from openviking.storage.vectordb.store.file_store import FileStore

COLLECTION_META = {
    "CollectionName": "demo",
    "Fields": [{"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True}],
}
INDEX_META = {
    "IndexName": "demo_index",
    "VectorIndex": {"IndexType": "flat"},
}


def _fail_writes(monkeypatch):
    monkeypatch.setattr(FileStore, "put", lambda self, key, value: False)


def test_persistent_dict_reports_failed_write(tmp_path, monkeypatch):
    dictionary = PersistentDict(str(tmp_path / "meta.json"), {"a": 1})
    _fail_writes(monkeypatch)

    assert dictionary.update({"b": 2}) is False
    assert dictionary.override({"c": 3}) is False
    # A failed write must not leave the in-memory state diverged from disk.
    assert dictionary.get_raw() == {"a": 1}


def test_persistent_dict_keeps_loaded_data_when_reopen_write_fails(tmp_path, monkeypatch):
    """Opening an existing metadata file must not depend on being able to rewrite it."""
    path = tmp_path / "meta.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")

    _fail_writes(monkeypatch)
    dictionary = PersistentDict(str(path))

    assert dictionary.get_raw() == {"a": 1}


def test_collection_meta_update_raises_when_write_fails(tmp_path, monkeypatch):
    meta = create_collection_meta(str(tmp_path / "collection.json"), COLLECTION_META)
    _fail_writes(monkeypatch)

    with pytest.raises(RuntimeError, match="Failed to persist collection metadata"):
        meta.update({"Description": "updated"})

    assert meta.get_meta_data().get("Description") is None


def test_index_meta_update_raises_when_write_fails(tmp_path, monkeypatch):
    collection_meta = create_collection_meta(str(tmp_path / "collection.json"), COLLECTION_META)
    index_meta = create_index_meta(collection_meta, str(tmp_path / "index.json"), INDEX_META)
    _fail_writes(monkeypatch)

    with pytest.raises(RuntimeError, match="Failed to persist index metadata"):
        index_meta.update({"Description": "updated"})

    assert index_meta.get_meta_data().get("Description") is None
