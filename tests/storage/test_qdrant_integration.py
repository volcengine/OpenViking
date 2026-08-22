# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import uuid

import pytest

from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.vectordb.collection.qdrant_collection import QdrantCollection
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestClient
from openviking.storage.vectordb.qdrant_utils import compile_qdrant_filter

QDRANT_URL = os.environ.get("QDRANT_URL")
requires_qdrant = pytest.mark.skipif(not QDRANT_URL, reason="QDRANT_URL not set")


@requires_qdrant
@pytest.mark.integration
def test_qdrant_phase_1_and_phase_2_round_trip() -> None:
    assert QDRANT_URL is not None
    suffix = uuid.uuid4().hex[:12]
    collection = QdrantCollection(
        client=QdrantRestClient(
            QDRANT_URL,
            api_key=os.environ.get("QDRANT_API_KEY"),
        ),
        collection_name=f"openviking_integration_{suffix}",
        metadata_collection_name=f"openviking_integration_{suffix}_meta",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        vector_dim=2,
        distance="cosine",
        sparse_enabled=True,
        sparse_weight=0.5,
    )
    try:
        collection.create_remote_collection(
            {
                "CollectionName": collection._collection_name,
                "Fields": [
                    {"FieldName": "account_id", "FieldType": "string"},
                    {"FieldName": "search_tags", "FieldType": "list<string>"},
                ],
            }
        )
        collection.create_index(
            "default",
            {"ScalarIndex": ["account_id", "search_tags"]},
        )
        collection.upsert_data(
            [
                {
                    "id": "doc-a",
                    "account_id": "acct-a",
                    "search_tags": ["team=search", "env=prod"],
                    "uri": "viking://resources/wiki/a.md",
                    "vector": [1.0, 0.0],
                    "sparse_vector": {"qdrant": 1.0},
                },
                {
                    "id": "doc-b",
                    "account_id": "acct-b",
                    "search_tags": ["team=search"],
                    "uri": "viking://resources/wiki/b.md",
                    "vector": [0.0, 1.0],
                    "sparse_vector": {"other": 1.0},
                },
            ]
        )

        path_and_account = compile_qdrant_filter(
            And(
                [
                    Eq("account_id", "acct-a"),
                    PathScope("uri", "viking://resources/wiki", depth=-1),
                ]
            )
        )
        assert collection.aggregate_data("default", filters=path_and_account).agg == {"_total": 1}
        assert collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0],
            filters=path_and_account,
            limit=2,
        ).data[0].id == "doc-a"
        assert collection.search_by_vector(
            "default",
            sparse_vector={"qdrant": 1.0},
            filters=path_and_account,
            limit=2,
        ).data[0].id == "doc-a"
        assert collection.search_by_vector(
            "default",
            dense_vector=[1.0, 0.0],
            sparse_vector={"qdrant": 1.0},
            filters=path_and_account,
            limit=2,
        ).data[0].id == "doc-a"
    finally:
        try:
            collection.drop()
        except Exception:
            pass
