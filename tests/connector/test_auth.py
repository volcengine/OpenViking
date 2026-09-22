# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""External OAuth contract, lifetime and identity regression checks."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from openviking.connector import auth
from openviking.connector.client import ConnectorClient
from openviking.parse.accessors.feishu_accessor import FeishuAccessor
from openviking.parse.understanding_api import UnderstandingAPI
from openviking.resource.feishu_watch_auth import (
    FeishuAppCredentials,
    FeishuOAuthClient,
    FeishuRefreshedToken,
)
from openviking_cli.exceptions import InternalError, InvalidArgumentError

REFERENCE = {
    "account_id": "cloud-account",
    "user_id": "cloud-user",
    "ov_user_id": "alice",
    "platform": "feishu_doc",
    "type": "oauth",
}


@pytest.fixture(autouse=True)
def external_endpoint(monkeypatch):
    monkeypatch.setattr(
        auth, "external_auth_url", lambda: "https://connector.example/oauth/access_token"
    )


def test_http_contract_and_secret_safe_errors(monkeypatch):
    responses = iter(
        [
            {"access_token": "user-token", "expires_at": 4102444800, "version": 2},
            {"code": 123, "message": "must-not-expose-secret"},
        ]
    )

    def handle(request):
        assert request.method == "POST"
        assert request.headers["x-api-key"] == "key"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == REFERENCE
        return httpx.Response(200, json=next(responses))

    client_type = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    provider = auth.ExternalFeishuToken(REFERENCE, "key")
    assert provider.get_token() == "user-token"
    provider._valid_until = 0
    with pytest.raises(InternalError) as error:
        provider.get_token()
    assert "must-not-expose-secret" not in str(error.value)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"access_token": ""},
        {"access_token": "x", "expires_at": 1},
        {"access_token": "x", "expires_at": "4102444800"},
        {"access_token": "x", "expires_at": float("nan")},
        {"access_token": "x", "expires_at": True},
    ],
)
def test_invalid_or_expired_tokens_never_fall_back(monkeypatch, data):
    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", lambda *_args: data)
    with pytest.raises(InternalError, match="invalid or expired"):
        auth.ExternalFeishuToken(REFERENCE, "key").get_token()


@pytest.mark.asyncio
async def test_long_import_reloads_token_and_scopes_are_isolated(monkeypatch):
    now = [1000]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: now[0]))
    fetch = Mock(
        side_effect=[
            {"access_token": "old", "expires_at": 1060},
            {"access_token": "new", "expires_at": 2000},
            {"access_token": "other-user", "expires_at": 2000},
        ]
    )
    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", fetch)
    provider = auth.ExternalFeishuToken(REFERENCE, "key")
    with auth.feishu_token_scope(provider):
        option = await asyncio.to_thread(FeishuAccessor._user_request_option, "snapshot")
        assert option.user_access_token == "old"
        assert provider.get_token() == "old"
        assert fetch.call_count == 1
        now[0] = 1031
        option = await asyncio.to_thread(FeishuAccessor._user_request_option, "snapshot")
        assert option.user_access_token == "new"
        api = object.__new__(UnderstandingAPI)
        assert await api._resolve_lark_file({"feishu_access_token": "snapshot"}) == {
            "user_access_token": "new"
        }

        async def other_task():
            with auth.feishu_token_scope(auth.ExternalFeishuToken(REFERENCE, "other-key")):
                return await asyncio.to_thread(FeishuAccessor._user_request_option, "snapshot")

        assert (await asyncio.create_task(other_task())).user_access_token == "other-user"
        assert auth.current_feishu_token.get() is provider
    assert auth.current_feishu_token.get() is None
    assert FeishuAccessor._user_request_option("explicit").user_access_token == "explicit"


@pytest.mark.parametrize(
    "reference", [None, {}, {**REFERENCE, "ov_user_id": "bob"}, {**REFERENCE, "platform": "git"}]
)
def test_reference_identity_is_checked(reference):
    with pytest.raises(InvalidArgumentError):
        auth.validate_oauth_ref(reference, "alice")


@pytest.mark.asyncio
async def test_external_endpoint_does_not_disable_local_refresh(monkeypatch):
    credentials = FeishuAppCredentials("app", "secret", "https://open.feishu.cn", 30)
    client = FeishuOAuthClient(credentials)
    refreshed = FeishuRefreshedToken("new-access", "new-refresh", 7200)
    refresh = Mock(return_value=refreshed)
    monkeypatch.setattr(client, "_refresh_user_access_token_sync", refresh)
    assert await client.refresh_user_access_token("local-refresh-token") == refreshed
    refresh.assert_called_once_with("local-refresh-token")


@pytest.mark.asyncio
async def test_partial_directory_auth_failure_never_reaches_finalization(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from unittest.mock import AsyncMock

    from openviking.parse.feishu_import import FeishuImportPlan
    from openviking.utils.resource_processor import ResourceProcessor

    accessor = FeishuAccessor()
    monkeypatch.setattr(
        accessor,
        "_list_drive_folder_children",
        Mock(
            return_value=[
                {"type": "file", "token": name, "name": name} for name in ("A.txt", "B.txt")
            ]
        ),
    )
    fetch = Mock(side_effect=[{"access_token": "valid"}, InternalError("auth unavailable")])
    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", fetch)
    provider = auth.ExternalFeishuToken(REFERENCE, "key")

    def download(token, **kwargs):
        accessor._user_request_option(kwargs["feishu_access_token"])
        provider._valid_until = 0
        return b"content", "text/plain", token

    monkeypatch.setattr(accessor, "_download_drive_file", download)

    async def parse(**_kwargs):
        skipped = []
        await accessor._materialize_drive_folder(
            "folder",
            tmp_path,
            feishu_access_token="snapshot",
            skipped_items=skipped,
            plan=FeishuImportPlan(tmp_path),
        )
        assert [p.name for p in tmp_path.iterdir()] == ["A.txt"]
        assert len(skipped) == 1
        return SimpleNamespace(temp_dir_path="viking://temp/partial")

    fs = SimpleNamespace(bind_request_context=lambda _: nullcontext(), delete_temp=AsyncMock())
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fs)
    processor = ResourceProcessor(Mock())
    processor._media_processor = SimpleNamespace(process=parse)
    processor.tree_builder.finalize_from_temp = AsyncMock()
    ctx = Mock()
    with auth.feishu_token_scope(provider), pytest.raises(InternalError, match="auth unavailable"):
        await processor.process_resource(
            path="https://example.feishu.cn/drive/folder/test", ctx=ctx
        )
    processor.tree_builder.finalize_from_temp.assert_not_awaited()
    fs.delete_temp.assert_awaited_once_with("viking://temp/partial", ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [None, "understanding_response_id", "understanding_file_id"])
@pytest.mark.parametrize("watch_interval", [0, 60])
async def test_queue_auth_failure_cleanup_and_prepared_results(
    monkeypatch, prepared, watch_interval
):
    from unittest.mock import AsyncMock

    from openviking.server.identity import RequestContext, Role
    from openviking.service.resource_service import ResourceService
    from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier("ov", "alice"), role=Role.USER)
    state = {"provider": auth.EXTERNAL_FEISHU_PROVIDER, "credentials": {}}
    service = ResourceService()
    service._connector_delegate = SimpleNamespace(
        restore_watch_request=AsyncMock(
            return_value=("key", "feishu_doc", {auth.OAUTH_REF_ARG: REFERENCE})
        )
    )
    service._execute_resource_ingestion = AsyncMock(return_value={"status": "success"})
    service._cleanup_reserved_target_if_empty = AsyncMock()
    fetch = Mock(side_effect=InternalError("auth unavailable"))
    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", fetch)
    msg = AddResourceMsg(
        task_id="task",
        path="https://example.feishu.cn/docx/doc",
        root_uri="viking://resources/reserved",
        account_id="ov",
        user_id="alice",
        role="user",
        cleanup_empty_target_on_failure=True,
        watch_interval=watch_interval,
        **({prepared: "submitted"} if prepared else {}),
    )
    lock = {"lease": "reserved"}
    job = service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=lock,
        stage_callback=AsyncMock(),
        task_auth=state,
    )
    if prepared:
        assert await job == {"status": "success"}
        fetch.assert_not_called()
        call = service._execute_resource_ingestion.await_args.kwargs
        assert "feishu_access_token" not in call
        assert call["watch_auth_state"] == (state if watch_interval else None)
        service._cleanup_reserved_target_if_empty.assert_not_awaited()
    else:
        with pytest.raises(InternalError, match="auth unavailable"):
            await job
        service._execute_resource_ingestion.assert_not_awaited()
        service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
            root_uri=msg.root_uri,
            ctx=ctx,
            resource_lock=lock,
        )
