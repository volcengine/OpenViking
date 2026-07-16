# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for the external Connector HTTP contract."""

from types import SimpleNamespace

import pytest

from openviking.connector import client as client_module
from openviking.connector.client import ConnectorClient, _unwrap_connector_response
from openviking_cli.exceptions import InternalError


class _FakeAsyncClient:
    response_payload = {}
    calls = []

    def __init__(self, *, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": self.timeout})
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: self.response_payload,
        )


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response_payload = {}
    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_submit_doc_add_sends_controlled_payload_and_auth_headers():
    _FakeAsyncClient.response_payload = {"code": 0, "data": {"task_key": "connector-1"}}
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info", "acct")

    result = await client.submit_doc_add(
        add_type="tos",
        api_key="secret",
        tos_path="bucket/prefix",
        path_prefix=["docs"],
        include_child=False,
        extra_params={"parser": "pdf", "api_key": "must-not-leak"},
    )

    assert result == {"task_key": "connector-1"}
    assert _FakeAsyncClient.calls == [
        {
            "url": "https://connector/doc/add",
            "json": {
                "add_type": "tos",
                "backend": "ov",
                "include_child": False,
                "tos_path": "bucket/prefix",
                "path_prefix": ["docs"],
                "parser": "pdf",
            },
            "headers": {
                "V-Account-Id": "acct",
                "Authorization": "Bearer secret",
            },
            "timeout": 30.0,
        }
    ]


@pytest.mark.asyncio
async def test_get_task_info_unwraps_task_object():
    _FakeAsyncClient.response_payload = {
        "code": 0,
        "data": {"Task": {"TaskKey": "connector-1", "Status": "running"}},
    }
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info")

    result = await client.get_task_info("connector-1", "Bearer secret")

    assert result == {"TaskKey": "connector-1", "Status": "running"}
    assert _FakeAsyncClient.calls[0]["json"] == {"TaskKey": "connector-1"}
    assert _FakeAsyncClient.calls[0]["headers"] == {"Authorization": "Bearer secret"}


def test_connector_business_error_is_not_treated_as_success():
    with pytest.raises(InternalError, match=r"code=1003.*permission denied"):
        _unwrap_connector_response({"code": 1003, "message": "permission denied", "data": {}})
