# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openviking_sdk import (
    AsyncHTTPClient,
    ContextPart,
    ImagePart,
    Message,
    SyncHTTPClient,
    TextPart,
    ToolPart,
)
from openviking_sdk.errors import NotFoundError


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AsyncHTTPClient, SyncHTTPClient])
@pytest.mark.parametrize(
    "method_name,user_id,http_method,result",
    [
        ("admin_add_group_member", "alice", "PUT", {"added": True}),
        ("admin_remove_group_member", "alice", "DELETE", {"removed": True}),
        ("admin_remove_group_member", "missing", "DELETE", {"removed": False}),
    ],
)
async def test_group_membership_clients_use_batch_http_contract(
    client_type, method_name, user_id, http_method, result
):
    def handle(request):
        assert request.method == http_method
        assert request.url.path == "/api/v1/admin/accounts/acme/groups/engineering/members"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"user_ids": [user_id]}
        return httpx.Response(200, json={"status": "ok", "result": result})

    client = client_type(url="http://localhost:1933")
    async_client = client if client_type is AsyncHTTPClient else client._async_client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        response = getattr(client, method_name)("acme", "engineering", user_id)
        if client_type is AsyncHTTPClient:
            response = await response
        assert response == result
    finally:
        await async_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AsyncHTTPClient, SyncHTTPClient])
@pytest.mark.parametrize(
    "method_name,args",
    [
        ("add_resource", ("https://example.com/doc.md", None, None, True)),
        ("add_skill", ({"name": "demo"}, True)),
        ("update_skill", ("demo", {"name": "demo"}, True)),
        ("write", ("viking://resources/demo.md", "hello", "replace", True)),
        (
            "batch_write",
            (
                "viking://resources/demo",
                [{"uri": "viking://resources/demo/a.md", "content": "hello"}],
                True,
            ),
        ),
    ],
)
async def test_write_clients_preserve_options_when_timeout_is_ignored(
    client_type, method_name, args
):
    client = client_type(url="http://localhost:1933")
    async_client = client if client_type is AsyncHTTPClient else client._async_client
    async_client._request = AsyncMock(return_value=object())
    async_client._handle_response_data = lambda _response: {"result": {"ok": True}}
    method = getattr(client, method_name)
    options = {"telemetry": {"enabled": True}}

    async def invoke(*positional, **keywords):
        result = method(*positional, **keywords)
        return await result if client_type is AsyncHTTPClient else result

    assert await invoke(*args, options=options) == {"ok": True}
    expected_request = async_client._request.await_args
    assert expected_request.kwargs["json"]["telemetry"] == options["telemetry"]
    assert "timeout" not in expected_request.kwargs["json"]
    for positional, keywords in (
        ((*args, options), {}),
        ((*args, 30, options), {}),
        (args, {"timeout": 30, "options": options}),
    ):
        async_client._request.reset_mock()
        assert await invoke(*positional, **keywords) == {"ok": True}
        assert async_client._request.await_args == expected_request

    async_client._request.reset_mock()
    with pytest.raises(TypeError, match="options were provided in both"):
        await invoke(*args, options, options=options)
    async_client._request.assert_not_awaited()
    assert options == {"telemetry": {"enabled": True}}


@pytest.mark.asyncio
async def test_async_http_client_initialize_forwards_event_hooks(sdk_transport):
    calls = []

    async def request_hook(request):
        calls.append(request.url.path)
        request.headers["X-Trace-ID"] = "sdk-trace"

    async def later_hook(request):
        calls.append("unexpected mutation")

    def handle(request):
        assert request.headers["X-Trace-ID"] == "sdk-trace"
        assert request.headers["Authorization"] == "Bearer header.payload.signature"
        return httpx.Response(200, json={"status": "ok"})

    sdk_transport(handle)
    event_hooks = {"request": [request_hook]}
    client = AsyncHTTPClient(
        url="http://localhost:1933",
        event_hooks=event_hooks,
        auth_mode="oidc",
        oidc_token="header.payload.signature",
    )
    event_hooks["request"].append(later_hook)
    await client.initialize()
    try:
        assert await client.health()
    finally:
        await client.close()

    assert calls == ["/health"]


def test_sync_http_client_batch_add_messages_posts_payload_to_escaped_session(sdk_transport):
    messages: list[Message] = [
        {
            "role": "user",
            "content": "hello",
            "peer_id": "explicit-user",
            "created_at": "2026-05-28T00:00:00+00:00",
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
    ]
    result = {"session_id": "thread#message", "message_count": 2, "added": 2}
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "result": result})

    sdk_transport(handle)
    client = SyncHTTPClient(url="http://localhost:1933")
    client.initialize()
    try:
        assert client.batch_add_messages("thread#message", messages) == result
    finally:
        client.close()

    (request,) = requests
    assert request.method == "POST"
    assert request.url.raw_path == b"/api/v1/sessions/thread%23message/messages/batch"
    assert json.loads(request.content) == {"messages": messages}


@pytest.mark.asyncio
async def test_async_http_client_sends_message_semantics_and_turn_retention():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}

    await client.add_message(
        "demo-session",
        role="assistant",
        parts=[{"type": "text", "text": "checking"}],
        options={
            "turn_id": "turn-1",
            "message_kind": "assistant_step",
            "source_message_ids": ["u1"],
        },
    )
    await client.commit_session(
        "demo-session",
        options={
            "retention_mode": "turn_budget",
            "enable_working_memory": True,
            "keep_recent_turn_count": 3,
            "retained_message_token_budget": 12_000,
            "min_raw_tail_steps": 1,
        },
    )

    assert fake_http.post.await_args_list[0].kwargs["json"] == {
        "role": "assistant",
        "parts": [{"type": "text", "text": "checking"}],
        "turn_id": "turn-1",
        "message_kind": "assistant_step",
        "source_message_ids": ["u1"],
    }
    assert fake_http.post.await_args_list[1].kwargs["json"] == {
        "keep_recent_count": 0,
        "enable_working_memory": True,
        "retention_mode": "turn_budget",
        "keep_recent_turn_count": 3,
        "retained_message_token_budget": 12_000,
        "min_raw_tail_steps": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_input", [None, {}, {"query": "OpenViking"}])
async def test_async_http_client_normalizes_message_part_objects(tool_input):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}

    await client.add_message(
        "demo-session",
        role="assistant",
        parts=[
            TextPart(text="checking"),
            ContextPart(
                uri="viking://resources/guide.md",
                context_type="resource",
                abstract="Guide",
            ),
            ImagePart(url="https://example.com/image.png", detail="high"),
            ToolPart(
                tool_id="call-1",
                tool_name="search",
                tool_input=tool_input,
                tool_status="completed",
                duration_ms=12.5,
                prompt_tokens=42,
                completion_tokens=7,
                tool_output_ref="viking://sessions/demo-session/tool-results/call-1",
                tool_output_truncated=True,
                tool_output_original_chars=1_024,
                tool_output_mime_type="application/json",
                tool_output_source_limit=512,
            ),
        ],
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/demo-session/messages",
        json={
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "checking"},
                {
                    "type": "context",
                    "uri": "viking://resources/guide.md",
                    "context_type": "resource",
                    "abstract": "Guide",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.com/image.png",
                        "detail": "high",
                    },
                },
                {
                    "type": "tool",
                    "tool_id": "call-1",
                    "tool_name": "search",
                    **({"tool_input": tool_input} if tool_input is not None else {}),
                    "tool_status": "completed",
                    "duration_ms": 12.5,
                    "prompt_tokens": 42,
                    "completion_tokens": 7,
                    "tool_output_ref": "viking://sessions/demo-session/tool-results/call-1",
                    "tool_output_truncated": True,
                    "tool_output_original_chars": 1_024,
                    "tool_output_mime_type": "application/json",
                    "tool_output_source_limit": 512,
                },
            ],
        },
    )


@pytest.mark.asyncio
async def test_async_http_client_add_message_serializes_flattened_peer_id():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}

    await client.add_message(
        "demo-session",
        role="assistant",
        content="checking",
        peer_id="peer-alice",
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/demo-session/messages",
        json={
            "role": "assistant",
            "content": "checking",
            "peer_id": "peer-alice",
        },
    )


@pytest.mark.asyncio
async def test_async_http_client_add_message_rejects_duplicate_peer_id():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}

    with pytest.raises(ValueError, match="peer_id"):
        await client.add_message(
            "demo-session",
            role="assistant",
            content="checking",
            peer_id="peer-alice",
            options={"peer_id": "peer-bob"},
        )

    fake_http.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_http_client_sends_event_memory_tag_configuration():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(
        post=AsyncMock(return_value=object()),
        patch=AsyncMock(return_value=object()),
    )
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}
    config = {"events": {"tags": ["team=search", "channel=web"]}}

    await client.create_session("tagged-session", options={"memory_extraction_config": config})
    await client.update_session_config(
        "tagged-session",
        {
            "memory_extraction_config": config,
            "auto_commit_policy": {"message_count_threshold": 25},
        },
    )
    await client.commit_session("tagged-session", options={"event_tags": []})
    await client.update_session_config("tagged-session", {"auto_commit_policy": None})
    await client.create_session("disabled-session", options={"auto_commit_policy": None})

    assert fake_http.post.await_args_list[0].kwargs["json"] == {
        "session_id": "tagged-session",
        "memory_extraction_config": config,
    }
    assert fake_http.patch.await_args_list[0].args == ("/api/v1/sessions/tagged-session/config",)
    assert fake_http.patch.await_args_list[0].kwargs["json"] == {
        "memory_extraction_config": config,
        "auto_commit_policy": {"message_count_threshold": 25},
    }
    assert fake_http.post.await_args_list[1].kwargs["json"] == {
        "keep_recent_count": 0,
        "extraction_metadata": {"event": {"tags": []}},
    }
    assert fake_http.patch.await_args_list[1].args == ("/api/v1/sessions/tagged-session/config",)
    assert fake_http.patch.await_args_list[1].kwargs["json"] == {"auto_commit_policy": None}
    assert fake_http.post.await_args_list[2].kwargs["json"] == {
        "session_id": "disabled-session",
        "auto_commit_policy": None,
    }


@pytest.mark.asyncio
async def test_async_http_client_reindex_sends_explicit_empty_tags():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"status": "completed"}

    await client.reindex(
        "viking://resources/demo",
        options={"tags": [], "tag_mode": "replace"},
    )

    assert fake_http.post.await_args.kwargs["json"]["tags"] == []
    assert fake_http.post.await_args.kwargs["json"]["tag_mode"] == "replace"


@pytest.mark.asyncio
async def test_async_http_client_reindex_sends_clear_without_tags():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"status": "completed"}

    await client.reindex(
        "viking://resources/demo",
        options={"tag_mode": "clear"},
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert "tags" not in payload
    assert payload["tag_mode"] == "clear"


@pytest.mark.asyncio
async def test_async_http_client_write_forwards_processing_mode():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"uri": "viking://resources/demo.md"}
    }

    await client.write(
        "viking://resources/demo.md",
        "updated",
        options={
            "processing_mode": "vectors_only",
            "defer_processing": True,
            "use_snapshot": True,
            "expected_sha256": "a" * 64,
        },
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["processing_mode"] == "vectors_only"
    assert payload["defer_processing"] is True
    assert payload["use_snapshot"] is True
    assert payload["expected_sha256"] == "a" * 64


@pytest.mark.asyncio
async def test_async_http_client_write_forwards_explicit_tags_and_mode():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {}}

    await client.write(
        "viking://resources/demo.md",
        "updated",
        options={"tags": [], "tag_mode": "replace"},
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["tags"] == []
    assert payload["tag_mode"] == "replace"


@pytest.mark.asyncio
async def test_async_http_client_write_forwards_clear_without_tags():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {}}

    await client.write(
        "viking://resources/demo.md",
        "updated",
        options={"tag_mode": "clear"},
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert "tags" not in payload
    assert payload["tag_mode"] == "clear"


@pytest.mark.asyncio
async def test_async_http_client_filesystem_tags_are_omission_aware():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response = lambda _response: []

    await client.ls("viking://resources")
    await client.tree("viking://resources", tags=["env=prod"])

    ls_params = client._request.await_args_list[0].kwargs["params"]
    tree_params = client._request.await_args_list[1].kwargs["params"]
    assert "tags" not in ls_params
    assert tree_params["tags"] == ["env=prod"]


@pytest.mark.asyncio
async def test_async_http_client_write_omits_default_processing_mode_for_legacy_servers():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"uri": "viking://resources/demo.md"}
    }

    await client.write("viking://resources/demo.md", "updated")

    payload = fake_http.post.await_args.kwargs["json"]
    assert "processing_mode" not in payload


def test_sync_http_client_forwards_tags_to_filesystem_methods():
    client = SyncHTTPClient(url="http://localhost:1933")
    entries = [{"name": "docs"}]
    page = {"result": entries, "has_more": True}

    with patch.object(client._async_client, "ls_page", return_value=page) as mock_ls:
        with patch.object(client._async_client, "tree_page", return_value=page) as mock_tree:
            with patch.object(client._async_client, "grep", return_value={}) as mock_grep:
                assert client.ls("viking://resources", tags=["env=prod"]) == entries
                assert (
                    client.tree(
                        "viking://resources",
                        tags=["env=prod"],
                        directories_only=True,
                    )
                    == entries
                )
                client.grep("viking://resources", "Sample", tags=["env=prod"])

    assert mock_ls.call_args.kwargs["tags"] == ["env=prod"]
    assert mock_tree.call_args.kwargs["tags"] == ["env=prod"]
    assert mock_tree.call_args.kwargs["directories_only"] is True
    assert mock_grep.call_args.kwargs["tags"] == ["env=prod"]


def test_sync_http_client_session_returns_sync_session_wrapper(sdk_transport):
    requests = []
    session_path = "/api/v1/sessions/demo-session"
    responses = {
        ("GET", session_path): {"session_id": "demo-session"},
        ("POST", f"{session_path}/messages"): {"message_id": "msg-1"},
        ("POST", f"{session_path}/commit"): {"task_id": "task-1"},
        ("GET", f"{session_path}/context"): {"messages": [{"role": "user"}]},
    }

    def handle(request):
        requests.append(request)
        result = responses.get((request.method, request.url.path))
        if result is None:
            return httpx.Response(
                404,
                json={
                    "status": "error",
                    "error": {
                        "code": "NOT_FOUND",
                        "details": {"resource": "missing", "type": "session"},
                    },
                },
            )
        return httpx.Response(200, json={"status": "ok", "result": result})

    sdk_transport(handle)
    client = SyncHTTPClient(url="http://localhost:1933")
    client.initialize()
    try:
        session = client.session("demo-session", must_exist=True)
        assert session.session_id == "demo-session"
        assert session.add_message("user", "hello", peer_id="alice") == {"message_id": "msg-1"}
        assert session.commit_async(keep_recent_count=3) == {"task_id": "task-1"}
        assert session.get_session_context(2048) == {"messages": [{"role": "user"}]}
        with pytest.raises(NotFoundError):
            client.session("missing", must_exist=True)
    finally:
        client.close()

    get, add, commit, context, missing = requests
    assert get.url.path == session_path
    assert json.loads(add.content) == {"role": "user", "content": "hello", "peer_id": "alice"}
    assert json.loads(commit.content) == {"keep_recent_count": 3}
    assert context.url.params["token_budget"] == "2048"
    assert missing.url.path == "/api/v1/sessions/missing"


def test_sync_http_client_compiles_with_per_call_instruction_and_model(sdk_transport):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "result": {"task_id": "cmp_1"}})

    sdk_transport(handle)
    client = SyncHTTPClient(url="http://localhost:1933")
    client.initialize()
    try:
        result = client.compile(
            ["viking://resources/source"],
            "viking://resources/output",
            "viking://agent/skills/wiki",
            options={
                "instruction": "Keep supporting evidence.",
                "args": {"model_name": "endpoint-1"},
            },
        )
    finally:
        client.close()

    assert result == {"task_id": "cmp_1"}
    (request,) = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/compile"
    assert json.loads(request.content) == {
        "from": ["viking://resources/source"],
        "to": "viking://resources/output",
        "skill": "viking://agent/skills/wiki",
        "instruction": "Keep supporting evidence.",
        "args": {"model_name": "endpoint-1"},
    }


@pytest.mark.parametrize(
    ("method_name", "format_value", "component"),
    [
        ("queue_status", None, "queue"),
        ("queue_status", "json", "queue"),
        ("vikingdb_status", "json", "vikingdb"),
        ("models_status", "table", "models"),
        ("get_status", None, "system"),
    ],
)
def test_http_client_observer_requests_support_optional_format(
    method_name, format_value, component
):
    result: dict[str, object] = {"is_healthy": True}
    if component != "system":
        result["name"] = component
        result["status"] = {"sample": 1} if format_value == "json" else "sample"
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "result": result})

    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        method = getattr(client, method_name)
        assert (method() if format_value is None else method(format=format_value)) == result
    finally:
        client.close()

    (request,) = requests
    assert request.method == "GET"
    assert request.url.path == f"/api/v1/observer/{component}"
    assert dict(request.url.params) == ({"format": format_value} if format_value else {})


@pytest.mark.asyncio
async def test_write_omits_removed_semantic_flags_from_http_payload():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"uri": "viking://resources/demo.md"}
    }

    await client.write("viking://resources/demo.md", "updated", wait=True)

    fake_http.post.assert_awaited_once_with(
        "/api/v1/content/write",
        json={
            "uri": "viking://resources/demo.md",
            "content": "updated",
            "mode": "replace",
            "wait": True,
        },
    )


@pytest.mark.asyncio
async def test_find_forwards_level_and_time_filters_when_provided():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    await client.find(
        "hello",
        options={
            "level": [0, 1],
            "since": "2026-01-01",
            "until": "2026-02-01",
            "time_field": "updated_at",
        },
    )

    payload = client._request.await_args.kwargs["json"]
    assert payload["level"] == [0, 1]
    assert payload["since"] == "2026-01-01"
    assert payload["until"] == "2026-02-01"
    assert payload["time_field"] == "updated_at"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_path"),
    [
        ("find", {}, "/api/v1/search/find"),
        ("search", {"session_id": "session-1"}, "/api/v1/search/search"),
    ],
)
async def test_retrieval_forwards_read_content(method_name, kwargs, expected_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    await getattr(client, method_name)("hello", options={"read_content": True}, **kwargs)

    assert client._request.await_args.args == ("POST", expected_path)
    assert client._request.await_args.kwargs["json"]["read_content"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "extra_kwargs", "expected_path"),
    [
        ("find", {}, "/api/v1/search/find"),
        ("search", {"session_id": "session-1"}, "/api/v1/search/search"),
        (
            "search_context",
            {"session_id": "session-1"},
            "/api/v1/search/search",
        ),
    ],
)
async def test_retrieval_accepts_image_as_explicit_parameter(
    method_name, extra_kwargs, expected_path
):
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    await getattr(client, method_name)(
        query="",
        image="viking://resources/query.png",
        **extra_kwargs,
    )

    assert client._request.await_args.args == ("POST", expected_path)
    payload = client._request.await_args.kwargs["json"]
    assert payload["image_url"] == "viking://resources/query.png"


@pytest.mark.asyncio
async def test_find_omits_level_and_time_filters_when_absent():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    await client.find("hello")

    payload = client._request.await_args.kwargs["json"]
    for key in ("level", "since", "until", "time_field"):
        assert key not in payload


@pytest.mark.asyncio
async def test_search_forwards_level_zero_and_omits_unset_time_filters():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    # level=0 is a valid level and must survive compaction (is-None check, not falsy).
    await client.search(
        "hello", session_id="s1", options={"level": 0, "events_time_decay_protection": "0"}
    )

    payload = client._request.await_args.kwargs["json"]
    assert payload["level"] == 0
    assert payload["session_id"] == "s1"
    assert payload["events_time_decay_protection"] == "0"
    for key in ("search_type", "since", "until", "time_field"):
        assert key not in payload


@pytest.mark.asyncio
async def test_search_and_search_context_forward_search_type():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    await client.search("OAuth token", options={"search_type": "keywords"})
    search_payload = client._request.await_args.kwargs["json"]
    assert search_payload["search_type"] == "keywords"

    await client.search_context("OAuth token", options={"search_type": "keywords"})
    context_payload = client._request.await_args.kwargs["json"]
    assert context_payload["search_type"] == "keywords"


@pytest.mark.asyncio
async def test_find_extra_forwards_unknown_fields_to_payload():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    # The escape hatch lets callers reach server fields the SDK does not yet
    # model, without waiting for an SDK release.
    await client.find("hello", options={"include_provenance": True})

    payload = client._request.await_args.kwargs["json"]
    assert payload["include_provenance"] is True


@pytest.mark.asyncio
async def test_write_extra_forwards_unknown_fields_to_payload():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {}}

    await client.write(
        "viking://resources/demo.md",
        "body",
        options={"extra": {"future_flag": 1, "resolve_conflicts": True}},
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["future_flag"] == 1
    assert payload["resolve_conflicts"] is True

    with pytest.raises(ValueError, match="extra cannot override 'resolve_conflicts'"):
        await client.write(
            "viking://resources/demo.md",
            "body",
            options={"resolve_conflicts": False, "extra": {"resolve_conflicts": True}},
        )


@pytest.mark.asyncio
async def test_add_skill_uploads_local_file_even_when_url_is_localhost(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: demo\ndescription: demo\n---\n\n# Demo\n")

    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http

    async def fake_upload(_path: str) -> str:
        return "upload_skill.md"

    client._upload_temp_file = fake_upload
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}

    await client.add_skill(str(skill_file))

    fake_http.post.assert_awaited_once()
    assert fake_http.post.await_args.kwargs["json"]["temp_file_id"] == "upload_skill.md"


@pytest.mark.asyncio
async def test_add_resource_uploads_local_file_even_when_url_is_localhost(tmp_path):
    resource_file = tmp_path / "demo.md"
    resource_file.write_text("# Demo\n")

    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http

    async def fake_upload(_path: str) -> str:
        return "upload_resource.md"

    client._upload_temp_file = fake_upload
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/demo"}
    }

    await client.add_resource(
        str(resource_file),
        options={"reason": "test", "watch_interval": 60},
    )

    fake_http.post.assert_awaited_once()
    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["temp_file_id"] == "upload_resource.md"
    assert payload["watch_interval"] == 60
    assert "path" not in payload


@pytest.mark.asyncio
async def test_add_resource_forwards_processing_mode():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/demo"}
    }

    await client.add_resource(
        "https://example.com/demo.md",
        options={"processing_mode": "vectors_only"},
    )

    fake_http.post.assert_awaited_once()
    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["processing_mode"] == "vectors_only"


@pytest.mark.asyncio
async def test_add_resource_forwards_declared_add_type_with_exact_target():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/feishu"}
    }

    await client.add_resource(
        "space:home",
        to="viking://resources/feishu",
        options={"add_type": " feishu "},
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["path"] == "space:home"
    assert payload["add_type"] == "feishu"
    assert payload["to"] == "viking://resources/feishu"


@pytest.mark.asyncio
async def test_add_resource_declared_add_type_requires_exact_target():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(ValueError, match="exact 'to'"):
        await client.add_resource("space:home", options={"add_type": "feishu"})


@pytest.mark.asyncio
async def test_add_resource_declared_add_type_rejects_parent():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(ValueError, match="'parent'"):
        await client.add_resource(
            "space:home",
            to="viking://resources/feishu",
            parent="viking://resources/imports",
            options={
                "add_type": "feishu",
            },
        )


@pytest.mark.asyncio
async def test_add_resource_declared_add_type_skips_local_file_upload(tmp_path):
    source = tmp_path / "source"
    source.write_text("connector source")

    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._upload_temp_file = AsyncMock(return_value="unexpected-upload")
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/feishu"}
    }

    await client.add_resource(
        str(source),
        to="viking://resources/feishu",
        options={"add_type": "feishu"},
    )

    client._upload_temp_file.assert_not_awaited()
    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["path"] == str(source)
    assert "temp_file_id" not in payload


def test_sync_add_resource_accepts_and_forwards_declared_add_type():
    client = SyncHTTPClient(url="http://127.0.0.1:1933")

    with patch.object(
        client._async_client,
        "add_resource",
        new_callable=AsyncMock,
        return_value={"root_uri": "viking://resources/feishu"},
    ) as mock_add_resource:
        result = client.add_resource(
            "space:home",
            to="viking://resources/feishu",
            options={"add_type": "feishu"},
        )

    assert result["root_uri"] == "viking://resources/feishu"
    assert mock_add_resource.await_args.kwargs == {
        "to": "viking://resources/feishu",
        "parent": None,
        "wait": False,
        "options": {"add_type": "feishu"},
    }


@pytest.mark.asyncio
async def test_add_resource_omits_default_processing_mode_for_legacy_servers():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/demo"}
    }

    await client.add_resource("https://example.com/demo.md")

    fake_http.post.assert_awaited_once()
    payload = fake_http.post.await_args.kwargs["json"]
    assert "processing_mode" not in payload


@pytest.mark.asyncio
async def test_admin_create_paths_accept_initial_user_config():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"status": "ok"}

    user_config = {"add_targets": {"resource_uri": "viking://user/resources/project-a"}}
    await client.admin_create_account("acct", "admin", user_config=user_config)
    await client.admin_register_user("acct", "alice", "admin", user_config=user_config)

    assert fake_http.post.await_args_list[0].kwargs["json"] == {
        "account_id": "acct",
        "admin_user_id": "admin",
        "user_config": user_config,
    }
    assert fake_http.post.await_args_list[1].kwargs["json"] == {
        "user_id": "alice",
        "role": "admin",
        "user_config": user_config,
    }


@pytest.mark.asyncio
async def test_admin_seed_payloads_are_sent():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"status": "ok"}

    await client.admin_create_account("acct", "admin", seed="admin-seed")
    await client.admin_register_user("acct", "alice", "admin", seed="alice-seed")
    await client.admin_regenerate_key("acct", "alice", seed="new-seed")

    assert fake_http.post.await_args_list[0].kwargs["json"] == {
        "account_id": "acct",
        "admin_user_id": "admin",
        "seed": "admin-seed",
    }
    assert fake_http.post.await_args_list[1].kwargs["json"] == {
        "user_id": "alice",
        "role": "admin",
        "seed": "alice-seed",
    }
    assert fake_http.post.await_args_list[2].kwargs["json"] == {"seed": "new-seed"}


@pytest.mark.asyncio
async def test_admin_get_account_uses_exact_path_and_returns_metadata():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response = lambda _response: {
        "account_id": "acct",
        "status": "active",
        "user_count": 2,
    }

    result = await client.admin_get_account("acct")

    client._request.assert_awaited_once_with("GET", "/api/v1/admin/accounts/acct")
    assert result["user_count"] == 2
    assert "api_key" not in result


@pytest.mark.asyncio
async def test_admin_get_user_reads_membership_from_point_read_path():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response = lambda _response: {"user_id": "alice", "role": "admin"}

    result = await client.admin_get_user("acct", "alice")
    client._request.assert_awaited_once_with("GET", "/api/v1/admin/accounts/acct/users/alice")
    assert result == {"user_id": "alice", "role": "admin"}
    assert "api_key" not in result and "key_prefix" not in result


@pytest.mark.asyncio
async def test_import_ovpack_uploads_local_file_even_when_url_is_localhost(tmp_path):
    pack_file = tmp_path / "demo.ovpack"
    pack_file.write_bytes(b"ovpack")

    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http

    async def fake_upload(_path: str) -> str:
        return "upload_pack.ovpack"

    client._upload_temp_file = fake_upload
    client._handle_response = lambda _response: {"uri": "viking://resources/imported"}

    await client.import_ovpack(
        str(pack_file),
        parent="viking://resources/",
        on_conflict="skip",
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/pack/import",
        json={
            "parent": "viking://resources/",
            "on_conflict": "skip",
            "temp_file_id": "upload_pack.ovpack",
        },
    )


@pytest.mark.asyncio
async def test_add_resource_sends_tags_and_tag_mode():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/demo"}
    }

    await client.add_resource(
        "https://example.com/demo.md",
        options={"tags": ["team=search"], "tag_mode": "append"},
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/resources",
        json={
            "wait": False,
            "path": "https://example.com/demo.md",
            "tags": ["team=search"],
            "tag_mode": "append",
        },
    )


@pytest.mark.asyncio
async def test_add_resource_sends_clear_without_tags():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/demo"}
    }

    await client.add_resource(
        "https://example.com/demo.md",
        options={"tag_mode": "clear"},
    )

    payload = fake_http.post.await_args.kwargs["json"]
    assert "tags" not in payload
    assert payload["tag_mode"] == "clear"


@pytest.mark.asyncio
async def test_find_uses_node_limit_as_http_limit_and_normalizes_target_uri_list():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"total": 0, "resources": []}}

    await client.find(
        "sample",
        target_uri=["/resources/demo", "viking://resources/kept"],
        limit=3,
        options={
            "node_limit": 9,
            "score_threshold": 0.4,
            "filter": {"type": "resource"},
            "context_type": "resource",
            "tags": ["k:v"],
            "telemetry": {"enabled": True},
        },
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/find",
        json={
            "query": "sample",
            "target_uri": ["viking://resources/demo", "viking://resources/kept"],
            "limit": 3,
            "node_limit": 9,
            "score_threshold": 0.4,
            "filter": {"type": "resource"},
            "context_type": "resource",
            "tags": ["k:v"],
            "telemetry": {"enabled": True},
        },
    )


@pytest.mark.asyncio
async def test_search_uses_session_wrapper_session_id_in_payload():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"total": 0, "resources": []}}

    await client.search(
        "sample",
        session_id="thread-123",
        target_uri="/resources/demo",
        limit=5,
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/search",
        json={
            "query": "sample",
            "target_uri": "viking://resources/demo",
            "session_id": "thread-123",
            "limit": 5,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("level_limit", [None, 0, 1])
async def test_grep_sends_optional_depth_through_public_clients(sync, level_limit):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "result": {"matches": []}})

    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if sync else client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    kwargs = {} if level_limit is None else {"level_limit": level_limit}
    try:
        if sync:
            result = client.grep("viking://resources", "needle", **kwargs)
        else:
            result = await client.grep("viking://resources", "needle", **kwargs)
        assert result == {"matches": []}
        assert len(requests) == 1
        assert requests[0].method == "POST"
        assert requests[0].url.path == "/api/v1/search/grep"
        payload = json.loads(requests[0].content)
        if level_limit is None:
            assert "level_limit" not in payload
        else:
            assert payload["level_limit"] == level_limit
    finally:
        await async_client.close()


@pytest.mark.asyncio
async def test_grep_normalizes_uri_and_exclude_uri():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"count": 0, "matches": []}

    await client.grep(
        "/resources/demo",
        pattern="Sample",
        case_insensitive=True,
        node_limit=12,
        exclude_uri="/resources/demo/tmp",
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/grep",
        json={
            "uri": "viking://resources/demo",
            "pattern": "Sample",
            "case_insensitive": True,
            "node_limit": 12,
            "exclude_uri": "viking://resources/demo/tmp",
        },
    )


@pytest.mark.asyncio
async def test_grep_omits_unset_tags_and_forwards_explicit_tags():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"count": 0, "matches": []}

    await client.grep("viking://resources", pattern="Sample")
    await client.grep("viking://resources", pattern="Sample", tags=["env=prod"])

    assert "tags" not in fake_http.post.await_args_list[0].kwargs["json"]
    assert fake_http.post.await_args_list[1].kwargs["json"]["tags"] == ["env=prod"]


@pytest.mark.asyncio
async def test_set_tags_clear_omits_tags_from_sdk_request():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"tags": []}}

    await client.set_tags("viking://resources/demo.md", mode="clear")

    fake_http.post.assert_awaited_once_with(
        "/api/v1/fs/attrs/set_tags",
        json={
            "uri": "viking://resources/demo.md",
            "mode": "clear",
            "recursive": False,
        },
    )


@pytest.mark.asyncio
async def test_glob_normalizes_scope_uri():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {
        "count": 1,
        "matches": ["viking://resources/demo.md"],
    }

    await client.glob("**/*.md", uri="/resources/")

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/glob",
        json={
            "pattern": "**/*.md",
            "uri": "viking://resources/",
            "node_limit": 256,
        },
    )


@pytest.mark.asyncio
async def test_glob_preserves_explicit_empty_extra_fields():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"count": 0, "matches": []}

    await client.glob("**/*.md", extra_fields=[])

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/glob",
        json={
            "pattern": "**/*.md",
            "uri": "viking://",
            "node_limit": 256,
            "extra_fields": [],
        },
    )


@pytest.mark.asyncio
async def test_glob_omits_unset_tags_and_forwards_tag_options():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"count": 0, "matches": []}

    await client.glob("**/*.md")
    await client.glob(
        "**/*.md",
        tags=["team=search", "env=prod"],
        include_tags=True,
    )

    plain_request = fake_http.post.await_args_list[0].kwargs["json"]
    tagged_request = fake_http.post.await_args_list[1].kwargs["json"]
    assert "tags" not in plain_request
    assert "include_tags" not in plain_request
    assert tagged_request["tags"] == ["team=search", "env=prod"]
    assert tagged_request["include_tags"] is True


@pytest.mark.asyncio
async def test_record_id_passthrough_is_limited_to_stat_and_read():
    record_id = "0123456789abcdef0123456789abcdef"
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response = lambda _response: {}

    await client.stat(record_id)
    await client.read(record_id)
    await client.rm(record_id)

    calls = client._request.await_args_list
    assert calls[0].kwargs["params"]["uri"] == record_id
    assert calls[1].kwargs["params"]["uri"] == record_id
    assert calls[2].kwargs["params"]["uri"] == f"viking://{record_id}"


@pytest.mark.asyncio
async def test_ls_and_tree_preserve_query_options_and_pagination():
    import httpx

    client = AsyncHTTPClient(url="http://localhost:1933")
    entries = [{"name": "docs"}]
    fake_http = SimpleNamespace(
        get=AsyncMock(
            return_value=httpx.Response(
                200, json={"status": "ok", "result": entries, "has_more": True}
            )
        )
    )
    client._http = fake_http

    page = await client.ls_page(
        "/resources/",
        simple=True,
        recursive=True,
        output="agent",
        abs_limit=32,
        show_all_hidden=True,
        node_limit=44,
        offset=5,
        limit=7,
        sort_by="mtime",
        sort_order="desc",
        include_abstract=False,
        include_overview=True,
        overview_limit=512,
    )
    assert page == {"result": entries, "has_more": True}
    fake_http.get.return_value = httpx.Response(200, json={"status": "ok", "result": entries})
    page = await client.tree_page(
        "viking://resources/",
        level_limit=2,
        offset=4,
        limit=6,
        directories_only=True,
    )
    assert page == {"result": entries, "has_more": False}
    assert await client.tree("viking://resources/", level_limit=0) == entries
    assert await client.tree("viking://resources/") == entries

    ls_call = fake_http.get.await_args_list[0]
    assert ls_call.args == ("/api/v1/fs/ls",)
    assert ls_call.kwargs == {
        "params": {
            "uri": "viking://resources/",
            "simple": True,
            "recursive": True,
            "output": "agent",
            "abs_limit": 32,
            "include_abstract": False,
            "include_overview": True,
            "overview_limit": 512,
            "show_all_hidden": True,
            "node_limit": 44,
            "offset": 5,
            "limit": 7,
            "sort_by": "mtime",
            "sort_order": "desc",
        },
    }
    assert fake_http.get.await_args_list[1].kwargs["params"]["offset"] == 4
    assert fake_http.get.await_args_list[1].kwargs["params"]["limit"] == 6
    assert [
        tree_call.kwargs["params"]["level_limit"] for tree_call in fake_http.get.await_args_list[1:]
    ] == [2, 0, 3]
    assert "include_abstract" not in fake_http.get.await_args_list[1].kwargs["params"]
    assert "include_overview" not in fake_http.get.await_args_list[1].kwargs["params"]
    assert fake_http.get.await_args_list[1].kwargs["params"]["overview_limit"] == 4000
    assert fake_http.get.await_args_list[1].kwargs["params"]["directories_only"] is True


@pytest.mark.asyncio
async def test_batch_write_serializes_processing_mode_and_operation_metadata():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response_data = lambda _response: {"result": {}}

    await client.batch_write(
        "viking://resources/wiki",
        [
            {
                "uri": "viking://resources/wiki/a.md",
                "content": "new",
                "expected_sha256": "a" * 64,
                "resolve_conflicts": True,
                "tags": ["team=search"],
                "tag_mode": "append",
                "acl": {"acl_mode": "restricted", "entries": []},
            }
        ],
        options={
            "processing_mode": "vectors_only",
            "skip_conflicts": True,
            "use_snapshot": True,
        },
    )

    payload = client._request.await_args.kwargs["json"]
    assert payload["processing_mode"] == "vectors_only"
    assert payload["skip_conflicts"] is True
    assert payload["use_snapshot"] is True
    assert payload["operations"][0]["expected_sha256"] == "a" * 64
    assert payload["operations"][0]["resolve_conflicts"] is True
    assert payload["operations"][0]["tags"] == ["team=search"]
    assert payload["operations"][0]["tag_mode"] == "append"
    assert payload["operations"][0]["acl"] == {"acl_mode": "restricted", "entries": []}


@pytest.mark.asyncio
async def test_watch_routes_support_uri_lookup_and_normalization():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(
        get=AsyncMock(return_value=object()),
        patch=AsyncMock(return_value=object()),
        delete=AsyncMock(return_value=object()),
        post=AsyncMock(return_value=object()),
    )
    client._http = fake_http
    client._handle_response = lambda _response: {"ok": True}

    await client.list_watches(active_only=True, to_uri="/resources/demo")
    await client.get_watch("task-1", to_uri="/resources/demo")
    await client.update_watch(
        to_uri="/resources/demo",
        watch_interval=30,
        is_active=False,
        reason="adjust",
        instruction="refresh",
    )
    await client.delete_watch(to_uri="/resources/demo")
    await client.trigger_watch(to_uri="/resources/demo")

    fake_http.get.assert_any_await(
        "/api/v1/watches",
        params={"active_only": True, "to_uri": "viking://resources/demo"},
    )
    fake_http.get.assert_any_await(
        "/api/v1/watches/task-1",
        params={"to_uri": "viking://resources/demo"},
    )
    fake_http.patch.assert_awaited_once_with(
        "/api/v1/watches",
        params={"to_uri": "viking://resources/demo"},
        json={
            "watch_interval": 30,
            "is_active": False,
            "reason": "adjust",
            "instruction": "refresh",
        },
    )
    fake_http.delete.assert_awaited_once_with(
        "/api/v1/watches",
        params={"to_uri": "viking://resources/demo"},
    )
    fake_http.post.assert_awaited_once_with(
        "/api/v1/watches/trigger",
        params={"to_uri": "viking://resources/demo"},
    )


@pytest.mark.asyncio
async def test_session_exists_returns_false_on_not_found(sdk_transport):
    def handle(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/sessions/missing-session"
        assert "auto_create" not in request.url.params
        return httpx.Response(
            404,
            json={
                "status": "error",
                "error": {"code": "NOT_FOUND", "message": "Session does not exist"},
            },
        )

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        assert await client.session_exists("missing-session") is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_session_wrapper_forwards_commit_context_and_archive_operations(sdk_transport):
    session_path = "/api/v1/sessions/thread-1"
    context = {"messages": [{"role": "user"}]}
    archive = {"archive_id": "arc-1"}
    responses = {
        ("POST", f"{session_path}/commit"): {"task_id": "task-1"},
        ("GET", f"{session_path}/context"): context,
        ("GET", f"{session_path}/archives/arc-1"): archive,
        ("DELETE", session_path): None,
    }
    requests = []

    def handle(request):
        requests.append(request)
        result = responses[request.method, request.url.path]
        return httpx.Response(200, json={"status": "ok", "result": result})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        session = client.session("thread-1")
        assert await session.commit(keep_recent_count=2) == {"task_id": "task-1"}
        assert await session.get_session_context(2048) == context
        assert await session.get_archive("arc-1") == archive
        await session.delete()
    finally:
        await client.close()

    commit, context_request, _, delete = requests
    assert json.loads(commit.content) == {"keep_recent_count": 2}
    assert context_request.url.params["token_budget"] == "2048"
    assert delete.method == "DELETE"


@pytest.mark.asyncio
async def test_export_and_backup_ovpack_append_default_suffixes(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    export_response = SimpleNamespace(is_success=True, content=b"exported")
    backup_response = SimpleNamespace(is_success=True, content=b"backup")
    fake_http = SimpleNamespace(post=AsyncMock(side_effect=[export_response, backup_response]))
    client._http = fake_http
    existing_export = tmp_path / "exports" / "demo.ovpack"
    existing_export.parent.mkdir()
    existing_export.write_bytes(b"old-backup")

    export_path = await client.export_ovpack("/resources/demo/", str(tmp_path / "exports" / "demo"))
    backup_path = await client.backup_ovpack(str(tmp_path / "backup-dir"))

    assert export_path.endswith("demo.ovpack")
    assert Path(export_path).read_bytes() == b"exported"
    assert backup_path.endswith("backup-dir.ovpack")
    assert Path(backup_path).read_bytes() == b"backup"


@pytest.mark.asyncio
async def test_backup_ovpack_preserves_existing_file_when_replace_fails(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._http = SimpleNamespace(
        post=AsyncMock(return_value=SimpleNamespace(is_success=True, content=b"new-backup"))
    )
    output = tmp_path / "backup.ovpack"
    output.write_bytes(b"known-good-backup")

    with patch("openviking_sdk.client.os.replace", side_effect=OSError("replace failed")):
        with pytest.raises(OSError, match="replace failed"):
            await client.backup_ovpack(str(output))

    assert output.read_bytes() == b"known-good-backup"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.asyncio
async def test_import_ovpack_fails_fast_when_local_file_is_missing(tmp_path, sdk_transport):
    sdk_transport(lambda request: pytest.fail(f"Unexpected HTTP request: {request.url}"))
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        with pytest.raises(FileNotFoundError, match="Local ovpack file not found"):
            await client.import_ovpack(
                str(tmp_path / "missing.ovpack"), parent="viking://resources/"
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_import_ovpack_fails_fast_when_path_is_directory(tmp_path, sdk_transport):
    sdk_transport(lambda request: pytest.fail(f"Unexpected HTTP request: {request.url}"))
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        with pytest.raises(ValueError, match="is not a file"):
            await client.import_ovpack(str(tmp_path), parent="viking://resources/")
    finally:
        await client.close()


def test_compile_configuration_and_named_execution():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._request = AsyncMock(return_value=object())
    client._async_client._handle_response = lambda _response: {"compile_name": "research-summary"}
    saved = client.create_compile_task(
        ["viking://resources/source"],
        "viking://resources/output",
        "viking://agent/skills/wiki",
        options={"args": {"auto_update": False}},
        compile_name="research-summary",
    )
    assert client._async_client._request.await_args.args == ("POST", "/api/v1/compile/create")
    assert client._async_client._request.await_args.kwargs["json"] == {
        "compile_name": "research-summary",
        "from": ["viking://resources/source"],
        "to": "viking://resources/output",
        "skill": "viking://agent/skills/wiki",
        "args": {"auto_update": False},
    }
    args = {"owner_user_id": "alice"}
    client.compile(compile_name=saved["compile_name"], options={"args": args})
    assert client._async_client._request.await_args.kwargs["json"] == {
        "compile_name": saved["compile_name"],
        "args": args,
    }
    with pytest.raises(ValueError, match="cannot be combined"):
        client.compile(compile_name=saved["compile_name"], to="viking://resources/other")
    with pytest.raises(ValueError, match="cannot be combined"):
        client.compile(compile_name=saved["compile_name"], options={"instruction": "override"})
    with pytest.raises(ValueError, match="requires"):
        client.compile()


@pytest.mark.parametrize("client_type", [AsyncHTTPClient, SyncHTTPClient])
async def test_compile_configuration_requires_a_name(client_type):
    client = client_type(url="http://localhost:1933")
    inputs = (
        ["viking://resources/source"],
        "viking://resources/output",
        "viking://agent/skills/wiki",
    )
    with pytest.raises(TypeError, match="compile_name"):
        client.create_compile_task(*inputs)
    with pytest.raises(ValueError, match="compile_name is required"):
        if client_type is AsyncHTTPClient:
            await client.create_compile_task(*inputs, compile_name="")
        else:
            client.create_compile_task(*inputs, compile_name="")


def test_compile_memory_omits_sources_without_losing_target():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._request = AsyncMock(return_value=object())
    client._async_client._handle_response = lambda _response: {"task_id": "cmp_memory"}
    client.compile(to="viking://~/memories", skill="memory")
    assert client._async_client._request.await_args.kwargs["json"] == {
        "from": [],
        "to": "viking://~/memories",
        "skill": "memory",
    }
