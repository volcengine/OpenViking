from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openviking_sdk import (
    AsyncHTTPClient,
    FindOptions,
    SearchContextOptions,
    SyncHTTPClient,
    WriteOptions,
)


def _client() -> tuple[AsyncHTTPClient, AsyncMock]:
    client = AsyncHTTPClient(url="http://localhost:1933")
    request = AsyncMock(return_value=object())
    client._http = SimpleNamespace(
        post=request,
        put=request,
    )
    client._handle_response_data = lambda _response: {"result": {"ok": True}}
    return client, request


def test_options_are_public_typed_dicts():
    find: FindOptions = {"limit": 0, "tags": []}
    context: SearchContextOptions = {"purpose": "coding", "max_tokens": 3000}
    write: WriteOptions = {"processing_mode": "vectors_only", "wait": False}

    assert find["limit"] == 0
    assert context["purpose"] == "coding"
    assert write["processing_mode"] == "vectors_only"


@pytest.mark.asyncio
async def test_find_serializes_options_and_preserves_explicit_empty_values():
    client, post = _client()

    await client.find(
        "authentication",
        {
            "target_uri": ["/resources/docs", "viking://user/memories"],
            "limit": 0,
            "level": 0,
            "tags": [],
            "include_provenance": False,
            "extra": {"future_flag": False},
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/search/find",
        json={
            "query": "authentication",
            "target_uri": [
                "viking://resources/docs",
                "viking://user/memories",
            ],
            "limit": 0,
            "level": 0,
            "tags": [],
            "include_provenance": False,
            "future_flag": False,
        },
    )


@pytest.mark.asyncio
async def test_search_context_sets_mode_and_context_fields():
    client, post = _client()

    await client.search_context(
        "continue refactor",
        {
            "session_id": "session-1",
            "purpose": "coding",
            "max_tokens": 3000,
            "dedup_turns": 5,
            "rewrite": "auto",
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/search/search",
        json={
            "query": "continue refactor",
            "mode": "context",
            "session_id": "session-1",
            "purpose": "coding",
            "max_tokens": 3000,
            "dedup_turns": 5,
            "rewrite": "auto",
        },
    )


@pytest.mark.asyncio
async def test_extra_cannot_override_official_or_fixed_fields():
    client, _post = _client()

    with pytest.raises(ValueError, match="limit"):
        await client.find("query", {"limit": 5, "extra": {"limit": 10}})

    with pytest.raises(ValueError, match="mode"):
        await client.reindex(
            "viking://resources",
            {"extra": {"mode": "prune_orphans"}},
        )

    with pytest.raises(ValueError, match="tags"):
        await client.reindex(
            "viking://resources",
            {"extra": {"tags": ["team=search"]}},
        )

    with pytest.raises(ValueError, match="mode"):
        await client.search_context("query", {"extra": {"mode": "list"}})

    with pytest.raises(ValueError, match="extraction_metadata"):
        await client.commit_session(
            "session-1",
            {
                "event_tags": ["team=search"],
                "extra": {"extraction_metadata": {}},
            },
        )


@pytest.mark.asyncio
async def test_add_message_prefers_parts_when_content_is_also_provided():
    client, post = _client()

    await client.add_message(
        "session-1",
        {
            "role": "assistant",
            "content": "fallback text",
            "parts": [{"type": "text", "text": "structured text"}],
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/sessions/session-1/messages",
        json={
            "role": "assistant",
            "parts": [{"type": "text", "text": "structured text"}],
        },
    )


@pytest.mark.asyncio
async def test_add_message_keeps_content_when_parts_is_null():
    client, post = _client()

    await client.add_message(
        "session-1",
        {
            "role": "assistant",
            "content": "fallback text",
            "parts": None,
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/sessions/session-1/messages",
        json={
            "role": "assistant",
            "content": "fallback text",
        },
    )


@pytest.mark.asyncio
async def test_add_message_uses_content_when_parts_is_empty():
    client, post = _client()

    await client.add_message(
        "session-1",
        {
            "role": "assistant",
            "content": "fallback text",
            "parts": [],
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/sessions/session-1/messages",
        json={
            "role": "assistant",
            "content": "fallback text",
        },
    )


@pytest.mark.asyncio
async def test_add_message_rejects_empty_parts_without_content():
    client, post = _client()

    with pytest.raises(ValueError, match="Either content or non-empty parts"):
        await client.add_message(
            "session-1",
            {
                "role": "assistant",
                "parts": [],
            },
        )

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_add_messages_normalizes_empty_and_nonempty_parts():
    client, post = _client()

    await client.batch_add_messages(
        "session-1",
        [
            {
                "role": "user",
                "content": "keep content",
                "parts": [],
            },
            {
                "role": "assistant",
                "content": "drop content",
                "parts": [{"type": "text", "text": "structured"}],
            },
        ],
    )

    post.assert_awaited_once_with(
        "/api/v1/sessions/session-1/messages/batch",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "keep content",
                },
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "structured"}],
                },
            ],
        },
    )


@pytest.mark.asyncio
async def test_batch_add_messages_rejects_empty_parts_without_content():
    client, post = _client()

    with pytest.raises(ValueError, match="Either content or non-empty parts"):
        await client.batch_add_messages(
            "session-1",
            [{"role": "user", "parts": []}],
        )

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_uses_options_and_extra():
    client, post = _client()

    await client.write(
        "/resources/note.md",
        "",
        {
            "mode": "replace",
            "wait": False,
            "processing_mode": "vectors_only",
            "extra": {"future_write_flag": 0},
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/content/write",
        json={
            "uri": "viking://resources/note.md",
            "content": "",
            "mode": "replace",
            "wait": False,
            "processing_mode": "vectors_only",
            "future_write_flag": 0,
        },
    )


@pytest.mark.asyncio
async def test_session_message_and_commit_options_use_latest_fields():
    client, post = _client()

    await client.add_message(
        "session-1",
        {
            "role": "assistant",
            "content": "done",
            "turn_id": "turn-1",
            "message_kind": "assistant_step",
            "source_message_ids": ["user-1"],
        },
    )
    await client.commit_session(
        "session-1",
        {
            "retention_mode": "turn_budget",
            "keep_recent_turn_count": 3,
            "retained_message_token_budget": 12_000,
            "min_raw_tail_steps": 1,
        },
    )

    assert post.await_args_list[0].kwargs["json"] == {
        "role": "assistant",
        "content": "done",
        "turn_id": "turn-1",
        "message_kind": "assistant_step",
        "source_message_ids": ["user-1"],
    }
    assert post.await_args_list[1].kwargs["json"] == {
        "retention_mode": "turn_budget",
        "keep_recent_turn_count": 3,
        "retained_message_token_budget": 12_000,
        "min_raw_tail_steps": 1,
    }


@pytest.mark.asyncio
async def test_turn_retention_fields_require_turn_budget_mode():
    client, _post = _client()

    with pytest.raises(ValueError, match="retention_mode"):
        await client.commit_session(
            "session-1",
            {"keep_recent_turn_count": 3},
        )


@pytest.mark.asyncio
async def test_add_resource_uses_options_and_normalizes_request_fields():
    client, post = _client()

    await client.add_resource(
        "https://example.com/manual.pdf",
        {
            "to": "/resources/manual.pdf",
            "create_parent": False,
            "wait": False,
            "processing_mode": "vectors_only",
            "tags": [],
            "tag_mode": "replace",
            "extra": {"future_ingest_flag": False},
        },
    )

    post.assert_awaited_once_with(
        "/api/v1/resources",
        json={
            "path": "https://example.com/manual.pdf",
            "to": "viking://resources/manual.pdf",
            "create_parent": False,
            "wait": False,
            "processing_mode": "vectors_only",
            "tags": [],
            "tag_mode": "replace",
            "future_ingest_flag": False,
        },
    )


@pytest.mark.asyncio
async def test_skill_writes_use_options_and_extra(tmp_path):
    client, post = _client()
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Demo")
    client._upload_temp_file = AsyncMock(return_value="skill-upload")

    await client.add_skill(
        str(skill_file),
        {"wait": False, "target_uri": "/user/skills", "extra": {"future": 0}},
    )
    await client.update_skill(
        "demo",
        {"name": "demo"},
        {"source_metadata": {}, "target_uri": "/agent/skills"},
    )

    assert post.await_args_list[0].kwargs["json"] == {
        "temp_file_id": "skill-upload",
        "wait": False,
        "target_uri": "viking://user/skills",
        "future": 0,
    }
    assert post.await_args_list[1].kwargs["json"] == {
        "data": {"name": "demo"},
        "source_metadata": {},
        "target_uri": "viking://agent/skills",
    }


@pytest.mark.asyncio
async def test_batch_messages_use_options_and_preserve_message_fields():
    client, post = _client()
    messages = [
        {
            "role": "assistant",
            "parts": [{"type": "text", "text": "done"}],
            "turn_id": "turn-1",
            "message_kind": "assistant_step",
        }
    ]

    await client.batch_add_messages(
        "session-1",
        messages,
        {"telemetry": False, "extra": {"future_batch_flag": 0}},
    )

    post.assert_awaited_once_with(
        "/api/v1/sessions/session-1/messages/batch",
        json={
            "messages": messages,
            "telemetry": False,
            "future_batch_flag": 0,
        },
    )


@pytest.mark.asyncio
async def test_unknown_official_option_suggests_extra():
    client, _post = _client()

    with pytest.raises(TypeError, match="use 'extra'"):
        await client.find("query", {"future_field": True})  # type: ignore[typeddict-unknown-key]


@pytest.mark.asyncio
async def test_legacy_keyword_options_are_merged_into_find_payload():
    client, post = _client()

    await client.find(
        "query",
        {"target_uri": "/resources", "limit": 0},
        level=2,
        tags=[],
    )

    post.assert_awaited_once_with(
        "/api/v1/search/find",
        json={
            "query": "query",
            "target_uri": "viking://resources",
            "limit": 0,
            "level": 2,
            "tags": [],
        },
    )


@pytest.mark.asyncio
async def test_legacy_keyword_options_reject_unknown_and_duplicate_fields():
    client, post = _client()

    with pytest.raises(TypeError, match=r"unsupported option 'limti'.*options\[\"extra\"\]"):
        await client.find("query", limti=5)

    with pytest.raises(ValueError, match=r"'limit'.*both options and kwargs"):
        await client.find("query", {"limit": 5}, limit=10)

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_keyword_options_preserve_session_null_and_message_fields():
    client, post = _client()

    await client.create_session(auto_commit_policy=None, session_id="session-1")
    await client.add_message(
        "session-1",
        role="assistant",
        content="fallback text",
        parts=[],
        turn_id="turn-1",
    )

    assert post.await_args_list[0].kwargs["json"] == {
        "auto_commit_policy": None,
        "session_id": "session-1",
    }
    assert post.await_args_list[1].kwargs["json"] == {
        "role": "assistant",
        "content": "fallback text",
        "turn_id": "turn-1",
    }


def test_sync_client_forwards_legacy_keyword_options():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client.find = AsyncMock(return_value={"total": 1})

    assert client.find("query", limit=0, level=2) == {"total": 1}

    client._async_client.find.assert_awaited_once_with(
        "query",
        None,
        limit=0,
        level=2,
    )


def test_sync_client_forwards_options_without_rebuilding_payload():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client.find = AsyncMock(return_value={"total": 1})
    client._async_client.search_context = AsyncMock(return_value={"rendered": "ctx"})
    client._async_client.write = AsyncMock(return_value={"uri": "viking://resources/a.md"})

    assert client.find("query", {"limit": 0}) == {"total": 1}
    assert client.search_context("query", {"max_tokens": 64}) == {"rendered": "ctx"}
    assert client.write("/resources/a.md", "", {"wait": False}) == {
        "uri": "viking://resources/a.md"
    }

    client._async_client.find.assert_awaited_once_with("query", {"limit": 0})
    client._async_client.search_context.assert_awaited_once_with("query", {"max_tokens": 64})
    client._async_client.write.assert_awaited_once_with("/resources/a.md", "", {"wait": False})


@pytest.mark.asyncio
async def test_agent_evolution_queries_normalize_experience_uri():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response = lambda _response: {"experience_uri": "ok"}

    await client.list_experience_trajectories(
        "/user/memories/experiences/a.md",
        {
            "limit": 25,
            "offset": 50,
            "start_date": "2026-08-01",
            "end_date": "2026-08-10",
        },
    )
    await client.get_experience_outcomes(
        "/user/memories/experiences/a.md",
        {"start_date": "2026-08-01", "end_date": "2026-08-10"},
    )

    assert client._request.await_args_list[0].args == (
        "GET",
        "/api/v1/agent-evolution/experiences/trajectories",
    )
    assert client._request.await_args_list[0].kwargs["params"] == {
        "experience_uri": "viking://user/memories/experiences/a.md",
        "limit": 25,
        "offset": 50,
        "start_date": "2026-08-01",
        "end_date": "2026-08-10",
    }
    assert client._request.await_args_list[1].kwargs["params"] == {
        "experience_uri": "viking://user/memories/experiences/a.md",
        "start_date": "2026-08-01",
        "end_date": "2026-08-10",
    }


@pytest.mark.asyncio
async def test_openviking_assets_resolve_and_preflight_latest_fields():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    client._handle_response = lambda _response: {"ok": True}

    await client.resolve_openviking_assets(
        "protocol: openviking-assets/1",
        {
            "manifest_label": "custom.yaml",
            "extra": {"future_flag": False},
        },
    )
    await client.preflight_openviking_asset(
        "private-repo",
        "https://github.com/example/private.git",
        {
            "branch": "main",
            "commit": "0123456789abcdef",
            "auth_config": {"username": "oauth2", "token": "secret"},
        },
    )

    assert client._request.await_args_list[0].args == (
        "POST",
        "/api/v1/openviking-assets/resolve",
    )
    assert client._request.await_args_list[0].kwargs["json"] == {
        "manifest_yaml": "protocol: openviking-assets/1",
        "manifest_label": "custom.yaml",
        "future_flag": False,
    }
    assert client._request.await_args_list[1].kwargs["json"] == {
        "name": "private-repo",
        "connector": "git",
        "repo_url": "https://github.com/example/private.git",
        "branch": "main",
        "commit": "0123456789abcdef",
        "auth_config": {"username": "oauth2", "token": "secret"},
    }
