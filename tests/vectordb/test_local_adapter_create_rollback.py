# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import shutil
from pathlib import Path

import pytest

from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter


class _FakeCollection:
    def __init__(self):
        self.create_index_calls = 0
        self.drop_calls = 0
        self.fail = True

    def create_index(self, _name, _meta):
        self.create_index_calls += 1
        if self.fail:
            raise RuntimeError("index creation failed")

    def drop(self):
        self.drop_calls += 1


def test_create_collection_discards_partial_collection_after_index_failure(tmp_path):
    adapter = LocalCollectionAdapter("context", str(tmp_path), "default")
    collection = _FakeCollection()

    def create_backend(_meta):
        collection_path = Path(adapter._collection_path())
        collection_path.mkdir(parents=True, exist_ok=True)
        (collection_path / "collection_meta.json").write_text("{}", encoding="utf-8")
        return collection

    adapter._create_backend_collection = create_backend
    adapter._sanitize_scalar_index_fields = lambda **kwargs: kwargs["scalar_index_fields"]
    adapter._build_default_index_meta = lambda **kwargs: {}

    with pytest.raises(RuntimeError, match="index creation failed"):
        adapter.create_collection(
            "context",
            {"Fields": []},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )

    assert collection.drop_calls == 1
    assert adapter._collection is None
    assert not (tmp_path / "context").exists()
    assert adapter.collection_exists() is False

    # The failed attempt must not poison later attempts on the same adapter.
    collection.fail = False
    assert (
        adapter.create_collection(
            "context",
            {"Fields": []},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        is True
    )
    assert collection.create_index_calls == 2


def test_create_collection_load_failure_keeps_existing_collection(tmp_path):
    adapter = LocalCollectionAdapter("context", str(tmp_path), "default")
    collection_path = Path(adapter._collection_path())
    collection_path.mkdir(parents=True)
    meta = collection_path / "collection_meta.json"
    meta.write_text('{"CollectionName": "context"}', encoding="utf-8")

    def fail_load():
        raise OSError("store is locked by another process")

    adapter._load_existing_collection_if_needed = fail_load

    with pytest.raises(OSError, match="locked"):
        adapter.create_collection(
            "context",
            {"Fields": []},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )

    # A failure to open an existing collection must never delete its data.
    assert meta.exists()
    assert meta.read_text(encoding="utf-8") == '{"CollectionName": "context"}'
    assert adapter._collection is None


def test_create_collection_keeps_healthy_existing_collection(tmp_path):
    adapter = LocalCollectionAdapter("context", str(tmp_path), "default")
    collection_path = Path(adapter._collection_path())
    collection_path.mkdir(parents=True)
    meta = collection_path / "collection_meta.json"
    meta.write_text("{}", encoding="utf-8")
    existing = _FakeCollection()
    adapter._load_existing_collection_if_needed = lambda: setattr(adapter, "_collection", existing)

    assert (
        adapter.create_collection(
            "context",
            {"Fields": []},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        is False
    )
    assert existing.drop_calls == 0
    assert meta.exists()


class _DeletingCollection:
    def __init__(self, collection_path: Path):
        self._collection_path = collection_path
        self.drop_calls = 0
        self.close_calls = 0

    def create_index(self, _name, _meta):
        raise RuntimeError("index creation failed")

    def close(self):
        self.close_calls += 1

    def drop(self):
        self.drop_calls += 1
        # The real PersistentCollection.drop() removes its whole directory.
        shutil.rmtree(self._collection_path, ignore_errors=True)


def test_create_collection_failure_keeps_preexisting_directory_contents(tmp_path):
    adapter = LocalCollectionAdapter("context", str(tmp_path), "default")
    collection_path = Path(adapter._collection_path())
    (collection_path / "recovery").mkdir(parents=True)
    note = collection_path / "operator-note.txt"
    note.write_text("keep me", encoding="utf-8")
    keep = collection_path / "recovery" / "keep.bin"
    keep.write_bytes(b"\x00\x01")
    collection = _DeletingCollection(collection_path)

    def create_backend(_meta):
        (collection_path / "collection_meta.json").write_text("{}", encoding="utf-8")
        return collection

    adapter._create_backend_collection = create_backend
    adapter._sanitize_scalar_index_fields = lambda **kwargs: kwargs["scalar_index_fields"]
    adapter._build_default_index_meta = lambda **kwargs: {}

    with pytest.raises(RuntimeError, match="index creation failed"):
        adapter.create_collection(
            "context",
            {"Fields": []},
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )

    # A directory that predates the attempt is closed, never dropped.
    assert collection.drop_calls == 0
    assert collection.close_calls == 1
    assert note.read_text(encoding="utf-8") == "keep me"
    assert keep.exists()
    assert not (collection_path / "collection_meta.json").exists()
    assert adapter._collection is None
