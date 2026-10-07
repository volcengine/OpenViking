# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filter deletes must not depend on ordering by ``updated_at`` on VikingDB backends."""

from types import SimpleNamespace
from typing import Any, Dict

import pytest

from openviking.storage.vectordb_adapters.base import CollectionAdapter


class _FakeCollection:
    """Returns ``matched`` records; optionally rejects scalar sorts like VikingDB."""

    def __init__(self, matched: list[str], *, reject_date_time_sort: bool):
        self.matched = matched
        self.reject_date_time_sort = reject_date_time_sort
        self.deleted: list[list[str]] = []
        self.probe_vectors: list[tuple[float, ...]] = []

    @staticmethod
    def _page(ids: list[str], limit: int, offset: int):
        items = [SimpleNamespace(id=i, fields={"id": i}, score=1.0) for i in ids]
        return SimpleNamespace(data=items[offset : offset + limit])

    def search_by_scalar(self, *, field, order, limit, offset, filters, output_fields, **_):
        if self.reject_date_time_sort and field == "updated_at":
            raise RuntimeError(
                "400 The field updated_at is not an int64 or float32 scalar index field"
            )
        return self._page(self.matched, limit, offset)

    def search_by_vector(self, *, dense_vector, limit, offset, filters, output_fields, **_):
        self.probe_vectors.append(tuple(dense_vector))
        return self._page(self.matched, limit, offset)

    def get_meta_data(self) -> Dict[str, Any]:
        return {"Fields": [{"FieldName": "vector", "Dim": 4}]}

    def delete_data(self, ids: list[str]) -> None:
        self.deleted.append(list(ids))


class _Adapter(CollectionAdapter):
    mode = "fake"
    _DATA_BATCH_SIZE = 2

    def __init__(self, collection: _FakeCollection, *, can_order_by_date_time: bool):
        super().__init__(collection_name="ctx")
        self._collection = collection
        self._CAN_ORDER_BY_DATE_TIME = can_order_by_date_time

    @classmethod
    def from_config(cls, config: Any) -> "_Adapter":
        raise NotImplementedError

    def _load_existing_collection_if_needed(self) -> None:
        return None

    def _create_backend_collection(self, meta: Dict[str, Any]):
        raise NotImplementedError


IDS = ["a", "b", "c", "d", "e"]
URI_FILTER = {"op": "must", "field": "uri", "conds": ["viking://resources/x/index.md"]}


def test_vikingdb_backend_deletes_without_sorting_by_updated_at():
    coll = _FakeCollection(IDS, reject_date_time_sort=True)
    adapter = _Adapter(coll, can_order_by_date_time=False)

    assert adapter.delete(filter=URI_FILTER) == 5
    assert [i for batch in coll.deleted for i in batch] == IDS
    # One probe vector for the whole enumeration keeps the pages stable.
    assert len(coll.probe_vectors) == 4
    assert len(set(coll.probe_vectors)) == 1


def test_sortable_backend_keeps_updated_at_pages():
    coll = _FakeCollection(IDS, reject_date_time_sort=False)
    adapter = _Adapter(coll, can_order_by_date_time=True)

    assert adapter.delete(filter=URI_FILTER) == 5
    assert [i for batch in coll.deleted for i in batch] == IDS
    assert coll.probe_vectors == []


def test_vikingdb_backend_regression_without_flag_would_fail():
    coll = _FakeCollection(IDS, reject_date_time_sort=True)
    adapter = _Adapter(coll, can_order_by_date_time=True)

    with pytest.raises(RuntimeError, match="updated_at is not an int64"):
        adapter.delete(filter=URI_FILTER)


@pytest.mark.parametrize(
    "module_name, class_name",
    [
        (
            "openviking.storage.vectordb_adapters.vikingdb_private_adapter",
            "VikingDBPrivateCollectionAdapter",
        ),
        ("openviking.storage.vectordb_adapters.volcengine_adapter", "VolcengineCollectionAdapter"),
    ],
)
def test_vikingdb_adapters_opt_out_of_date_time_ordering(module_name, class_name):
    module = __import__(module_name, fromlist=[class_name])
    assert getattr(module, class_name)._CAN_ORDER_BY_DATE_TIME is False
