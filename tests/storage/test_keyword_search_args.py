# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"result": {"data": []}}


class _CapturingClient:
    def __init__(self):
        self.requests = []

    def do_req(self, method, path, req_body=None):
        self.requests.append((method, path, req_body))
        return _FakeResponse()


def _collection():
    collection = object.__new__(VikingDBCollection)
    collection.project_name = "default"
    collection.collection_name = "context"
    collection.client = _CapturingClient()
    return collection


def test_keyword_search_sends_advanced_args_in_request_body():
    collection = _collection()

    collection.search_by_keywords(
        index_name="default",
        query="OAuth token",
        mode="bm25",
        fields=["content"],
        limit=5,
        bm25_k1=1.2,
        bm25_b=0.75,
        advance={"post_process_input_limit": 100},
    )

    _, path, body = collection.client.requests[0]
    assert path == "/api/vikingdb/data/search/keywords"
    assert body["bm25_k1"] == 1.2
    assert body["bm25_b"] == 0.75
    assert body["advance"] == {"post_process_input_limit": 100}


def test_keyword_search_omits_advanced_args_when_unset():
    collection = _collection()

    collection.search_by_keywords(
        index_name="default",
        query="OAuth token",
        mode="bm25",
        fields=["content"],
        limit=5,
    )

    _, _, body = collection.client.requests[0]
    assert "bm25_k1" not in body
    assert "bm25_b" not in body
    assert "advance" not in body
