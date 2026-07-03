from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient
from openviking_sdk.client import Session, SyncSession
from openviking_sdk.errors import NotFoundError


@pytest.mark.asyncio
async def test_async_http_client_batch_add_messages_posts_batch_payload():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"session_id": "batch-session", "message_count": 2, "added": 2}
    }

    messages = [
        {
            "role": "user",
            "content": "hello",
            "peer_id": "explicit-user",
            "created_at": "2026-05-28T00:00:00+00:00",
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
    ]

    result = await client.batch_add_messages("batch-session", messages)

    assert result == {"session_id": "batch-session", "message_count": 2, "added": 2}
    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/batch-session/messages/batch",
        json={"messages": messages},
    )


@pytest.mark.asyncio
async def test_async_http_client_batch_add_messages_url_encodes_session_id():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"session_id": "encoded-session", "message_count": 1, "added": 1}
    }

    session_id = (
        "feishu__cli_a938e530eb7c9bd9__"
        "oc_aa9e08fddf5727f9c53400a07ff505cd#om_x100b6ff6c3df48ace10030ac68d3eb4"
    )

    await client.batch_add_messages(session_id, [{"role": "user", "content": "hello"}])

    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/"
        "feishu__cli_a938e530eb7c9bd9__"
        "oc_aa9e08fddf5727f9c53400a07ff505cd%23om_x100b6ff6c3df48ace10030ac68d3eb4"
        "/messages/batch",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )


@pytest.mark.asyncio
async def test_async_http_client_reindex_posts_content_reindex():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"status": "completed"}

    result = await client.reindex(
        "viking://resources/demo",
        mode="vectors_only",
        wait=False,
    )

    assert result == {"status": "completed"}
    fake_http.post.assert_awaited_once_with(
        "/api/v1/content/reindex",
        json={
            "uri": "viking://resources/demo",
            "mode": "vectors_only",
            "wait": False,
        },
    )


def test_sync_http_client_reindex_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    with patch.object(
        client._async_client,
        "reindex",
        return_value={"status": "accepted"},
    ) as mock_reindex:
        with patch(
            "openviking_sdk.client.run_async",
            return_value={"status": "accepted"},
        ) as mock_run:
            result = client.reindex(
                "viking://resources/demo",
                mode="vectors_only",
                wait=False,
            )

    assert result == {"status": "accepted"}
    assert mock_run.called
    assert mock_reindex.called


def test_sync_http_client_batch_add_messages_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    messages = [
        {
            "role": "user",
            "content": "hello",
            "peer_id": "explicit-user",
            "created_at": "2026-05-28T00:00:00+00:00",
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
    ]

    with patch.object(
        client._async_client,
        "batch_add_messages",
        return_value={"session_id": "batch-session", "message_count": 2, "added": 2},
    ) as mock_batch:
        with patch(
            "openviking_sdk.client.run_async",
            return_value={"session_id": "batch-session", "message_count": 2, "added": 2},
        ) as mock_run:
            result = client.batch_add_messages("batch-session", messages)

    assert result == {"session_id": "batch-session", "message_count": 2, "added": 2}
    assert mock_run.called
    mock_batch.assert_called_once_with("batch-session", messages)


def test_sync_http_client_session_returns_sync_session_wrapper():
    client = SyncHTTPClient(url="http://localhost:1933")

    session = client.session("demo-session")

    assert isinstance(session, SyncSession)
    assert session.session_id == "demo-session"


def test_sync_session_add_message_wraps_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    session = client.session("demo-session")

    with patch.object(
        client._async_client,
        "add_message",
        return_value={"message_id": "msg-1"},
    ) as mock_add_message:
        with patch(
            "openviking_sdk.client.run_async",
            return_value={"message_id": "msg-1"},
        ) as mock_run:
            result = session.add_message("user", content="hello")

    assert result == {"message_id": "msg-1"}
    assert mock_run.called
    mock_add_message.assert_called_once_with(
        "demo-session",
        role="user",
        content="hello",
        parts=None,
        created_at=None,
        peer_id=None,
    )


def test_sync_session_commit_and_context_are_sync():
    client = SyncHTTPClient(url="http://localhost:1933")
    session = client.session("demo-session")

    with patch.object(
        client._async_client,
        "commit_session",
        return_value={"status": "completed"},
    ) as mock_commit:
        with patch.object(
            client._async_client,
            "get_session_context",
            return_value={"messages": []},
        ) as mock_context:
            with patch(
                "openviking_sdk.client.run_async",
                side_effect=[{"status": "completed"}, {"messages": []}],
            ) as mock_run:
                commit_result = session.commit(keep_recent_count=1)
                context_result = session.get_session_context(2048)

    assert commit_result == {"status": "completed"}
    assert context_result == {"messages": []}
    assert mock_run.call_count == 2
    mock_commit.assert_called_once_with("demo-session", keep_recent_count=1)
    mock_context.assert_called_once_with("demo-session", 2048)


def test_sync_http_client_declares_common_sync_methods_explicitly():
    explicit_methods = SyncHTTPClient.__dict__

    for method_name in [
        "add_message",
        "create_session",
        "list_sessions",
        "get_session",
        "get_session_context",
        "delete_session",
        "search",
        "find",
        "grep",
        "glob",
        "ls",
        "tree",
        "read",
        "write",
        "add_resource",
        "add_skill",
        "import_ovpack",
        "export_ovpack",
        "list_watches",
        "get_watch",
        "update_watch",
        "delete_watch",
        "trigger_watch",
        "list_skills",
        "get_skill",
        "update_skill",
        "delete_skill",
        "get_task",
        "list_tasks",
        "admin_list_accounts",
    ]:
        assert method_name in explicit_methods, method_name


def test_sync_http_client_session_must_exist_checks_existence():
    client = SyncHTTPClient(url="http://localhost:1933")

    with patch.object(
        client, "get_session", return_value={"session_id": "demo-session"}
    ) as mock_get:
        session = client.session("demo-session", must_exist=True)

    assert isinstance(session, SyncSession)
    assert session.session_id == "demo-session"
    mock_get.assert_called_once_with("demo-session")


def test_sync_http_client_session_must_exist_propagates_not_found():
    client = SyncHTTPClient(url="http://localhost:1933")

    with patch.object(
        client,
        "get_session",
        side_effect=NotFoundError("missing-session", "session"),
    ) as mock_get:
        with pytest.raises(NotFoundError):
            client.session("missing-session", must_exist=True)

    mock_get.assert_called_once_with("missing-session")


def test_sync_session_commit_async_and_repr_match_sync_usage():
    client = SyncHTTPClient(url="http://localhost:1933")
    session = client.session("demo-session")

    with patch.object(session, "commit", return_value={"status": "completed"}) as mock_commit:
        result = session.commit_async(keep_recent_count=3)

    assert result == {"status": "completed"}
    mock_commit.assert_called_once_with(telemetry=False, keep_recent_count=3)
    assert "demo-session" in repr(session)


def test_sync_http_client_get_status_does_not_require_run_async():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._get_system_status = AsyncMock(return_value={"is_healthy": True})

    status = client.get_status()

    assert status == {"is_healthy": True}


def test_sync_http_client_health_wraps_async_coroutine():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client.health = AsyncMock(return_value=True)

    assert client.health() is True


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
            "timeout": None,
            "telemetry": False,
        },
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

    await client.add_resource(str(resource_file), reason="test", watch_interval=60)

    fake_http.post.assert_awaited_once()
    payload = fake_http.post.await_args.kwargs["json"]
    assert payload["temp_file_id"] == "upload_resource.md"
    assert payload["watch_interval"] == 60
    assert "path" not in payload


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
async def test_find_uses_node_limit_as_http_limit_and_normalizes_target_uri_list():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"total": 0, "resources": []}}

    await client.find(
        query="sample",
        target_uri=["/resources/demo", "viking://resources/kept"],
        limit=3,
        node_limit=9,
        score_threshold=0.4,
        filter={"type": "resource"},
        context_type="resource",
        tags=["k:v"],
        telemetry={"enabled": True},
    )

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/find",
        json={
            "query": "sample",
            "target_uri": ["viking://resources/demo", "viking://resources/kept"],
            "limit": 9,
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

    session = Session(client, "thread-123")
    await client.search(query="sample", target_uri="/resources/demo", session=session, limit=5)

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/search",
        json={
            "query": "sample",
            "target_uri": "viking://resources/demo",
            "session_id": "thread-123",
            "limit": 5,
            "telemetry": False,
        },
    )


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
async def test_glob_normalizes_scope_uri():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {
        "count": 1,
        "matches": ["viking://resources/demo.md"],
    }

    await client.glob("*.md", uri="/resources/")

    fake_http.post.assert_awaited_once_with(
        "/api/v1/search/glob",
        json={"pattern": "*.md", "uri": "viking://resources/"},
    )


@pytest.mark.asyncio
async def test_ls_passes_full_query_params():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(get=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: []

    await client.ls(
        "/resources/",
        simple=True,
        recursive=True,
        output="agent",
        abs_limit=32,
        show_all_hidden=True,
        node_limit=44,
    )

    fake_http.get.assert_awaited_once_with(
        "/api/v1/fs/ls",
        params={
            "uri": "viking://resources/",
            "simple": True,
            "recursive": True,
            "output": "agent",
            "abs_limit": 32,
            "show_all_hidden": True,
            "node_limit": 44,
        },
    )


@pytest.mark.asyncio
async def test_rm_uses_delete_request_with_timeout_when_provided():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(request=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: None

    await client.rm("/resources/demo.md", recursive=True, wait=True, timeout=5.0)

    fake_http.request.assert_awaited_once_with(
        "DELETE",
        "/api/v1/fs",
        params={
            "uri": "viking://resources/demo.md",
            "recursive": True,
            "wait": True,
            "timeout": 5.0,
        },
    )


@pytest.mark.asyncio
async def test_link_normalizes_single_and_multiple_target_uris():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: None

    await client.link("/resources/from", ["/resources/a", "viking://resources/b"], reason="demo")

    fake_http.post.assert_awaited_once_with(
        "/api/v1/relations/link",
        json={
            "from_uri": "viking://resources/from",
            "to_uris": ["viking://resources/a", "viking://resources/b"],
            "reason": "demo",
        },
    )


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
async def test_session_exists_returns_false_on_not_found():
    client = AsyncHTTPClient(url="http://localhost:1933")

    async def raise_not_found(_session_id: str, *, auto_create: bool = False):
        raise NotFoundError("demo", "session")

    client.get_session = raise_not_found

    assert await client.session_exists("missing-session") is False


@pytest.mark.asyncio
async def test_session_wrapper_forwards_commit_context_and_archive_operations():
    client = AsyncHTTPClient(url="http://localhost:1933")
    session = Session(client, "thread-1")
    client.commit_session = AsyncMock(return_value={"status": "completed"})
    client.get_session_context = AsyncMock(return_value={"messages": []})
    client.get_session_archive = AsyncMock(return_value={"archive_id": "arc-1"})
    client.delete_session = AsyncMock(return_value=None)

    commit_result = await session.commit(keep_recent_count=2)
    context_result = await session.get_session_context(2048)
    archive_result = await session.get_archive("arc-1")
    await session.delete()

    assert commit_result == {"status": "completed"}
    assert context_result == {"messages": []}
    assert archive_result == {"archive_id": "arc-1"}
    client.commit_session.assert_awaited_once_with("thread-1", keep_recent_count=2)
    client.get_session_context.assert_awaited_once_with("thread-1", 2048)
    client.get_session_archive.assert_awaited_once_with("thread-1", "arc-1")
    client.delete_session.assert_awaited_once_with("thread-1")


@pytest.mark.asyncio
async def test_export_and_backup_ovpack_append_default_suffixes(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    export_response = SimpleNamespace(is_success=True, content=b"exported")
    backup_response = SimpleNamespace(is_success=True, content=b"backup")
    fake_http = SimpleNamespace(post=AsyncMock(side_effect=[export_response, backup_response]))
    client._http = fake_http

    export_path = await client.export_ovpack("/resources/demo/", str(tmp_path / "exports" / "demo"))
    backup_path = await client.backup_ovpack(str(tmp_path / "backup-dir"))

    assert export_path.endswith("demo.ovpack")
    assert Path(export_path).read_bytes() == b"exported"
    assert backup_path.endswith("backup-dir.ovpack")
    assert Path(backup_path).read_bytes() == b"backup"


@pytest.mark.asyncio
async def test_import_ovpack_fails_fast_when_local_file_is_missing(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http

    missing_path = tmp_path / "missing.ovpack"

    with pytest.raises(FileNotFoundError, match="Local ovpack file not found"):
        await client.import_ovpack(str(missing_path), parent="viking://resources/")


@pytest.mark.asyncio
async def test_import_ovpack_fails_fast_when_path_is_directory(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http

    pack_dir = tmp_path / "pack_dir"
    pack_dir.mkdir()

    with pytest.raises(ValueError, match="is not a file"):
        await client.import_ovpack(str(pack_dir), parent="viking://resources/")
