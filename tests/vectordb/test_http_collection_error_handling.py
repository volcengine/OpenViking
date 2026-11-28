# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP vectordb write calls must surface backend failures.

The embedding pipeline treats a non-empty upsert result as success and acks
the queue message. Returning an empty list on an HTTP or business error made
the message disappear while the vector was never stored.
"""

import json
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest

from openviking.storage.vectordb.collection.http_collection import HttpCollection

_POST = "openviking.storage.vectordb.collection.http_collection.requests.post"


def _response(status_code: int, body: Dict[str, Any]) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.text = json.dumps(body)
    return response


def _collection() -> HttpCollection:
    return HttpCollection(meta_data={"CollectionName": "test-collection"})


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
def test_http_write_raises_on_http_error(method: str):
    with patch(_POST, return_value=_response(500, {"message": "backend down"})):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            getattr(_collection(), method)([{"id": "doc-1"}])


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
def test_http_write_raises_on_business_error(method: str):
    with patch(_POST, return_value=_response(200, {"code": 1001, "message": "write rejected"})):
        with pytest.raises(RuntimeError, match="code 1001"):
            getattr(_collection(), method)([{"id": "doc-1"}])


def test_http_upsert_returns_ids_on_success():
    with patch(_POST, return_value=_response(200, {"code": 0, "data": ["doc-1"]})):
        assert _collection().upsert_data([{"id": "doc-1"}]) == ["doc-1"]


def test_http_update_returns_data_on_success():
    with patch(_POST, return_value=_response(200, {"code": 0, "data": ["doc-1"]})):
        assert _collection().update_data([{"id": "doc-1"}]) == ["doc-1"]
