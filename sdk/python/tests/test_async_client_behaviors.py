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
from openviking_sdk.errors import ConflictError, NotFoundError


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AsyncHTTPClient, SyncHTTPClient])
async def test_add_mcp_preserves_native_connection_payload(client_type):
    headers = {"Authorization": "Bearer native-token", "Accept": "application/json"}

    def handle(request):
        expected = {
            "name": "weather",
            "url": "https://mcp.example.com",
            "headers": headers,
            "target_uri": "viking://agent/tools",
            "wait": False,
        }
        assert request.url.path == "/api/v1/mcps"
        assert json.loads(request.content) == expected
        return httpx.Response(200, json={"status": "ok", "result": {"valid": True}})

    client = client_type(url="http://localhost:1933")
    async_client = client if client_type is AsyncHTTPClient else client._async_client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        result = client.add_mcp(
            "weather",
            "https://mcp.example.com",
            headers=headers,
            target_uri="agent/tools",
        )
        if client_type is AsyncHTTPClient:
            result = await result
        assert result == {"valid": True}
    finally:
        await async_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AsyncHTTPClient, SyncHTTPClient])
async def test_validate_mcp_sends_only_native_connection_info(client_type):
    headers = {"Authorization": "Bearer native-token"}

    def handle(request):
        assert request.url.path == "/api/v1/mcps/validate"
        assert json.loads(request.content) == {
            "name": "weather",
            "url": "http://mcp.example.com",
            "headers": headers,
        }
        return httpx.Response(200, json={"status": "ok", "result": {"valid": True}})

    client = client_type(url="http://localhost:1933")
    async_client = client if client_type is AsyncHTTPClient else client._async_client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        result = client.validate_mcp("weather", "http://mcp.example.com", headers=headers)
        if client_type is AsyncHTTPClient:
            result = await result
        assert result == {"valid": True}
    finally:
        await async_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AsyncHTTPClient, SyncHTTPClient])
@pytest.mark.parametrize("timeout_seconds", [None, 65.5])
async def test_invoke_mcp_tool_timeout_payload(client_type, timeout_seconds):
    def handle(request):
        assert request.url.path == "/api/v1/mcps/invoke"
        expected = {
            "uri": "viking://agent/tools/weather/current.json",
            "arguments": {"city": "Beijing"},
        }
        if timeout_seconds is not None:
            expected["timeout_seconds"] = timeout_seconds
        assert json.loads(request.content) == expected
        return httpx.Response(200, json={"status": "ok", "result": {"content": []}})

    client = client_type(url="http://localhost:1933")
    async_client = client if client_type is AsyncHTTPClient else client._async_client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        result = client.invoke_mcp_tool(
            "viking://agent/tools/weather/current.json",
            {"city": "Beijing"},
            timeout_seconds=timeout_seconds,
        )
        if client_type is AsyncHTTPClient:
            result = await result
        assert result == {"content": []}
    finally:
        await async_client.close()


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
async def test_write_omits_legacy_defaults_and_preserves_explicit_empty_content(sdk_transport):
    requests = []
    document = "viking://resources/demo.md"

    def handle(request):
        assert (request.method, request.url.path) == ("POST", "/api/v1/content/write")
        body = json.loads(request.content)
        requests.append(body)
        if body.get("resolve_conflicts"):
            return httpx.Response(
                409,
                json={
                    "status": "error",
                    "error": {"code": "CONFLICT", "message": "revision changed"},
                },
            )
        return httpx.Response(200, json={"status": "ok", "result": {"uri": document}})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        result = await client.write(document, "")
        assert result["uri"] == document
        with pytest.raises(ConflictError, match="revision changed"):
            await client.write(
                document,
                "resolved",
                options={
                    "use_snapshot": True,
                    "resolve_conflicts": True,
                    "processing_mode": "vectors_only",
                },
            )
    finally:
        await client.close()
    assert requests[0]["content"] == ""
    assert {"processing_mode", "regenerate_semantics", "revectorize"}.isdisjoint(requests[0])
    assert requests[1]["use_snapshot"] is True
    assert requests[1]["processing_mode"] == "vectors_only"


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
async def test_connector_resource_requires_an_exact_target_before_transport(sdk_transport):
    requests = []
    target = "viking://resources/feishu"

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "result": {"root_uri": target}})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        with pytest.raises(ValueError, match="exact 'to'"):
            await client.add_resource("space:home", options={"add_type": "feishu"})
        with pytest.raises(ValueError, match="'parent'"):
            await client.add_resource(
                "space:home",
                to=target,
                parent="viking://resources/imports",
                options={"add_type": "feishu"},
            )
        result = await client.add_resource(
            "space:home", to=target, options={"add_type": " feishu "}
        )
    finally:
        await client.close()

    assert result["root_uri"] == target
    (request,) = requests
    assert request.url.path == "/api/v1/resources"
    assert json.loads(request.content) == {
        "path": "space:home",
        "to": target,
        "add_type": "feishu",
        "wait": False,
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
async def test_retrieval_preserves_scope_list_session_and_explicit_level(sdk_transport):
    requests = []

    def handle(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "ok", "result": {"resources": []}})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        await client.find(
            "sample",
            target_uri=["/resources/demo", "viking://resources/kept"],
            options={"node_limit": 9, "level": 0, "read_content": True},
        )
        await client.search("sample", session_id="thread-123", options={"search_type": "keywords"})
    finally:
        await client.close()

    (find_path, find), (search_path, search) = requests
    assert find_path == "/api/v1/search/find"
    assert find["target_uri"] == ["viking://resources/demo", "viking://resources/kept"]
    assert find["node_limit"] == 9
    assert find["level"] == 0
    assert find["read_content"] is True
    assert search_path == "/api/v1/search/search"
    assert search["session_id"] == "thread-123"
    assert search["search_type"] == "keywords"
    assert "level" not in search


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
    assert all(
        tree_call.kwargs["params"]["abs_limit"] == 2000
        for tree_call in fake_http.get.await_args_list[1:]
    )
    await client.ls("viking://resources/")
    assert fake_http.get.await_args.kwargs["params"]["abs_limit"] == 2000


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
async def test_watch_patch_distinguishes_omitted_fields_from_explicit_pause(sdk_transport):
    def handle(request):
        assert (request.method, request.url.path) == ("PATCH", "/api/v1/watches")
        assert request.url.params["to_uri"] == "viking://resources/demo"
        assert json.loads(request.content) == {"is_active": False}
        return httpx.Response(200, json={"status": "ok", "result": {"is_active": False}})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        assert await client.update_watch(to_uri="/resources/demo", is_active=False) == {
            "is_active": False
        }
    finally:
        await client.close()


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
