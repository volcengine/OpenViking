# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Keyword search must tolerate either ``keywords`` or ``query`` being omitted.

``CollectionAdapter.search_by_keywords`` is called in two shapes: the collection
API passes ``keywords``, while OpenViking's own retrieval paths pass ``query``
only. The debug line used to call ``len()`` on both unconditionally, so every
``search_type="keywords"`` request raised ``TypeError`` before reaching the
engine.
"""

from types import SimpleNamespace

from openviking.storage.vectordb_adapters.base import CollectionAdapter


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def search_by_keywords(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[])


class _Adapter(CollectionAdapter):
    @classmethod
    def from_config(cls, config):
        raise NotImplementedError

    def _load_existing_collection_if_needed(self) -> None:
        return None

    def _create_backend_collection(self, meta):
        raise NotImplementedError


def _adapter() -> _Adapter:
    adapter = _Adapter("context")
    adapter._collection = _FakeCollection()
    return adapter


def test_query_only_keyword_search_does_not_raise():
    adapter = _adapter()

    assert adapter.search_by_keywords(query="OAuth token", limit=5) == []

    assert adapter._collection.calls[0]["keywords"] is None
    assert adapter._collection.calls[0]["query"] == "OAuth token"
    assert adapter._collection.calls[0]["limit"] == 5


def test_keywords_only_keyword_search_does_not_raise():
    adapter = _adapter()

    assert adapter.search_by_keywords(keywords=["oauth"], limit=5) == []

    assert adapter._collection.calls[0]["keywords"] == ["oauth"]
    assert adapter._collection.calls[0]["query"] is None
