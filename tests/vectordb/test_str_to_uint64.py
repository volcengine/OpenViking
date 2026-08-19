# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import openviking.storage.vectordb.utils.str_to_uint64 as subject
from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)


def test_str_to_uint64_preserves_stable_utf8_hashes():
    assert subject.str_to_uint64("abc") == 4952883123889572249
    assert subject.str_to_uint64("部署") == 4215394785105220848


def test_str_to_uint64_passes_utf8_bytes_to_xxhash(monkeypatch):
    seen = []

    class _Digest:
        @staticmethod
        def intdigest():
            return 42

    def _xxh64(value):
        seen.append(value)
        return _Digest()

    monkeypatch.setattr(subject.xxhash, "xxh64", _xxh64)

    assert subject.str_to_uint64("部署") == 42
    assert seen == ["部署".encode("utf-8")]


def test_local_collection_round_trips_string_primary_keys():
    collection = get_or_create_local_collection(
        meta_data={
            "CollectionName": "string_pk_compat",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
    )

    inserted = collection.upsert_data(
        [
            {
                "id": "部署-1",
                "uri": "viking://resources/string-pk",
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ]
    )
    fetched = collection.fetch_data(["部署-1"])
    collection.delete_data(["部署-1"])
    missing = collection.fetch_data(["部署-1"])

    assert inserted.ids == ["部署-1"]
    assert [(item.id, item.fields["uri"]) for item in fetched.items] == [
        ("部署-1", "viking://resources/string-pk")
    ]
    assert missing.ids_not_exist == ["部署-1"]
