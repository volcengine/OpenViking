# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for the external Connector HTTP contract."""

import json
from types import SimpleNamespace

import httpx
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
        to="viking://resources/docs/report.pdf",
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
                "to": "viking://resources/docs/report.pdf",
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
async def test_submit_doc_add_forwards_external_connector_stream_states():
    _FakeAsyncClient.response_payload = {"code": 0, "data": {"task_key": "connector-1"}}
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info", "acct")
    stream_states = {"documents": {"cursor": {"sync_checkpoint": "2026-09-01T00:00:00+00:00"}}}

    await client.submit_doc_add(
        add_type="tos",
        api_key="secret",
        tos_path="bucket/prefix",
        to="viking://resources/imports",
        stream_states=stream_states,
    )

    assert _FakeAsyncClient.calls[0]["json"]["stream_states"] == stream_states


@pytest.mark.asyncio
async def test_submit_doc_add_carries_non_tos_source_in_param_config():
    _FakeAsyncClient.response_payload = {"code": 0, "data": {"task_key": "connector-1"}}
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info", "acct")

    await client.submit_doc_add(
        add_type="git",
        api_key="secret",
        to="viking://resources/imports/repo",
        param_config={
            "repo_url": "https://git.example/org/repo.git",
            "branch": "release",
        },
    )

    payload = _FakeAsyncClient.calls[0]["json"]
    assert payload == {
        "add_type": "git",
        "backend": "ov",
        "include_child": True,
        "to": "viking://resources/imports/repo",
        "param_config": {
            "repo_url": "https://git.example/org/repo.git",
            "branch": "release",
        },
    }
    assert "tos_path" not in payload


@pytest.mark.asyncio
async def test_submit_doc_add_forwards_resource_tags():
    _FakeAsyncClient.response_payload = {"code": 0, "data": {"task_key": "connector-1"}}
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info", "acct")

    await client.submit_doc_add(
        add_type="tos",
        api_key="secret",
        tos_path="bucket/prefix",
        to="viking://resources/imports",
        extra_params={
            "tags": ["team=search", "env=test"],
            "tag_mode": "append",
        },
    )

    payload = _FakeAsyncClient.calls[0]["json"]
    assert payload["tags"] == ["team=search", "env=test"]
    assert payload["tag_mode"] == "append"


@pytest.mark.asyncio
async def test_submit_doc_add_keeps_credentials_out_of_param_config():
    _FakeAsyncClient.response_payload = {"code": 0, "data": {"task_key": "connector-1"}}
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info", "acct")

    await client.submit_doc_add(
        add_type="git",
        api_key="secret",
        to="viking://resources/imports/repo",
        param_config={"repo_url": "https://git.example/org/private.git"},
        auth_config={"token": "ghp-secret", "username": "oauth2"},
    )

    payload = _FakeAsyncClient.calls[0]["json"]
    assert payload["auth_config"] == {"token": "ghp-secret", "username": "oauth2"}
    assert payload["param_config"] == {"repo_url": "https://git.example/org/private.git"}


@pytest.mark.asyncio
async def test_get_task_info_unwraps_task_object():
    stream_states = {"documents": {"cursor": {"sync_checkpoint": "2026-09-01T00:00:00Z"}}}
    _FakeAsyncClient.response_payload = {
        "code": 0,
        "data": {
            "Task": {"TaskKey": "connector-1", "Status": "running"},
            "StreamStates": stream_states,
        },
    }
    client = ConnectorClient("https://connector/doc/add", "https://tracker/task/info")

    result = await client.get_task_info("connector-1", "Bearer secret")

    assert result == {
        "TaskKey": "connector-1",
        "Status": "running",
        "StreamStates": stream_states,
    }
    assert _FakeAsyncClient.calls[0]["json"] == {"TaskKey": "connector-1"}
    assert _FakeAsyncClient.calls[0]["headers"] == {"Authorization": "Bearer secret"}


def test_connector_business_error_is_not_treated_as_success():
    with pytest.raises(InternalError, match=r"code=1003.*permission denied"):
        _unwrap_connector_response({"code": 1003, "message": "permission denied", "data": {}})


def test_resolve_connector_credential_posts_reference_with_api_key(monkeypatch):
    reference = {
        "account_id": "acct",
        "resource_id": "ov-resource",
        "user_id": "alice",
        "ov_user_id": "ov-user",
        "type": "oauth",
        "platform": "dingtalk",
        "binding_version": 3,
    }

    def handle(request):
        assert request.method == "POST"
        assert request.headers["x-api-key"] == "user-key"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == reference
        return httpx.Response(
            200,
            json={"code": 0, "data": {"auth_config": {"user_access_token": "live"}}},
        )

    client_type = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = ConnectorClient("", "", "acct")

    data = client.resolve_connector_credential(
        "https://connector.example/api/v1/inner/connector/credential/resolve",
        "user-key",
        reference,
    )

    assert data == {"auth_config": {"user_access_token": "live"}}


def test_resolve_connector_credential_hides_remote_error(monkeypatch):
    def handle(request):
        return httpx.Response(200, json={"code": 42, "message": "must-not-expose-secret"})

    client_type = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = ConnectorClient("", "", "acct")

    with pytest.raises(InternalError) as error:
        client.resolve_connector_credential("https://connector.example/resolve", "k", {})
    assert "must-not-expose-secret" not in str(error.value)
