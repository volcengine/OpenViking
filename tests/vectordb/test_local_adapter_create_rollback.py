# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

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
