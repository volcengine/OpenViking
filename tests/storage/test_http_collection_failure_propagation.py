# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.storage.vectordb.collection.http_collection import HttpCollection


def _collection(monkeypatch) -> HttpCollection:
    response = SimpleNamespace(status_code=503, text="unavailable")
    monkeypatch.setattr(
        "openviking.storage.vectordb.collection.http_collection.requests.post",
        lambda *args, **kwargs: response,
    )
    monkeypatch.setattr(
        "openviking.storage.vectordb.collection.http_collection.requests.get",
        lambda *args, **kwargs: response,
    )
    return HttpCollection(
        ip="127.0.0.1",
        port=1933,
        meta_data={"ProjectName": "default", "CollectionName": "context"},
    )


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda collection: collection.update({"Fields": []}), "update collection"),
        (lambda collection: collection.get_meta_data(), "get collection metadata"),
        (lambda collection: collection.list_indexes(), "list indexes"),
        (lambda collection: collection.update_index("default"), "update index"),
        (lambda collection: collection.get_index_meta_data("default"), "get index metadata"),
        (lambda collection: collection.drop_index("default"), "drop index"),
        (lambda collection: collection.drop(), "drop collection"),
        (lambda collection: collection.delete_all_data(), "delete all data"),
    ],
)
def test_management_failures_raise_instead_of_reporting_an_empty_result(monkeypatch, call, message):
    collection = _collection(monkeypatch)

    with pytest.raises(RuntimeError, match=message):
        call(collection)
