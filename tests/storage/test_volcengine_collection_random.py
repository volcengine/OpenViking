# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.errors import ConnectionError
from openviking.storage.vectordb.collection.volcengine_collection import VolcengineCollection


class _FakeResponse:
    status_code = 429
    text = '{"ResponseMetadata":{"Error":{"Code":"TooManyRequests","Message":"rate limited"}}}'

    def json(self):
        return {
            "ResponseMetadata": {
                "Error": {
                    "Code": "TooManyRequests",
                    "Message": "rate limited",
                }
            }
        }


class _FailingDataClient:
    def do_req(self, *_args, **_kwargs):
        return _FakeResponse()


def test_volcengine_search_by_random_raises_on_non_200_response():
    collection = object.__new__(VolcengineCollection)
    collection.project_name = "default"
    collection.collection_name = "context"
    collection.data_client = _FailingDataClient()

    with pytest.raises(ConnectionError, match="TooManyRequests"):
        collection.search_by_random(index_name="idx")
