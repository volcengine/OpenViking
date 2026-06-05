# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""SDK tests using AsyncHTTPClient against a real uvicorn server."""

import asyncio

import httpx
import pytest
import pytest_asyncio

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.exceptions import ConflictError, FailedPreconditionError, ProcessingError
from tests.server.conftest import SAMPLE_MD_CONTENT, TEST_TMP_DIR
from tests.server.ovpack_test_helpers import build_ovpack_bytes


@pytest_asyncio.fixture()
async def http_client(running_server):
    """Create an AsyncHTTPClient connected to the running server."""
    port, svc, sdk_user_key = running_server
    client = AsyncHTTPClient(
        url=f"http://127.0.0.1:{port}",
        api_key=sdk_user_key,
        account="",
        user="",
        timeout=33.0,
        extra_headers={},
        profile_enabled=False,
    )
    await client.initialize()
    yield client, svc
    await client.close()


# ===================================================================
# Lifecycle
# ===================================================================


async def test_sdk_health(http_client):
    client, _ = http_client
    assert await client.health() is True


# ===================================================================
# Resources
# ===================================================================


async def test_sdk_add_resource(http_client):
    client, _ = http_client
    f = TEST_TMP_DIR / "sdk_sample.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SAMPLE_MD_CONTENT)

    result = await client.add_resource(path=str(f), reason="sdk test", wait=True)
    assert "usage" not in result
    assert "telemetry" not in result
    assert "root_uri" in result
    assert result["root_uri"].startswith("viking://")


async def test_sdk_add_resource_raises_processing_error_for_business_error(
    http_client,
    monkeypatch,
):
    client, svc = http_client

    async def fake_add_resource(**kwargs):
        return {
            "status": "error",
            "errors": ["Parse error: boom"],
        }

    monkeypatch.setattr(svc.resources, "add_resource", fake_add_resource)

    with pytest.raises(ProcessingError, match="Parse error: boom"):
        await client.add_resource(path="https://example.com/bad.md", wait=True)


def test_sdk_maps_conflict_error_envelope():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    response = httpx.Response(
        409,
        json={
            "status": "error",
            "error": {
                "code": "CONFLICT",
                "message": "URI viking://resources/demo already has a reindex in progress",
            },
        },
    )

    with pytest.raises(ConflictError, match="already has a reindex in progress") as exc_info:
        client._handle_response_data(response)

    assert exc_info.value.code == "CONFLICT"


async def test_sdk_add_skill_from_local_file(http_client):
    client, _ = http_client
    f = TEST_TMP_DIR / "sdk_skill.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        """---
name: sdk-skill
description: SDK localhost upload test
---

# SDK Skill
"""
    )

    result = await client.add_skill(data=str(f), wait=True)
    assert "root_uri" in result
    assert "uri" in result
    assert result["root_uri"] == result["uri"]
    assert result["uri"].startswith("viking://user/sdk_test_user/skills/")


async def test_sdk_import_ovpack_from_local_file(http_client):
    client, _ = http_client
    f = TEST_TMP_DIR / "sdk_import.ovpack"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(build_ovpack_bytes())

    uri = await client.import_ovpack(
        str(f),
        parent="viking://resources/imported/",
        on_conflict="overwrite",
    )
    assert uri.startswith("viking://resources/imported/")


async def test_sdk_wait_processed(http_client):
    client, _ = http_client
    result = await client.wait_processed()
    assert isinstance(result, dict)


# ===================================================================
# Filesystem
# ===================================================================


async def test_sdk_ls(http_client):
    client, _ = http_client
    result = await client.ls("viking://")
    assert isinstance(result, list)


async def test_sdk_mkdir_and_ls(http_client):
    client, _ = http_client
    await client.mkdir("viking://resources/sdk_dir/")
    result = await client.ls("viking://resources/")
    assert isinstance(result, list)


async def test_sdk_mkdir_with_description_sets_abstract(http_client):
    client, _ = http_client
    uri = "viking://resources/sdk_dir_desc/"
    description = "SDK directory description"

    await client.mkdir(uri, description=description)

    abstract = await client.abstract(uri)
    assert abstract == description


async def test_sdk_tree(http_client):
    client, _ = http_client
    result = await client.tree("viking://")
    assert isinstance(result, list)


# ===================================================================
# Sessions
# ===================================================================


async def test_sdk_session_lifecycle(http_client):
    client, _ = http_client

    # Create
    session_info = await client.create_session()
    session_id = session_info["session_id"]
    assert session_id

    # Add message
    msg_result = await client.add_message(session_id, "user", "Hello from SDK")
    assert msg_result["message_count"] == 1

    # Get
    info = await client.get_session(session_id)
    assert info["session_id"] == session_id

    context = await client.get_session_context(session_id)
    assert context["latest_archive_overview"] == ""
    assert context["pre_archive_abstracts"] == []
    assert [m["parts"][0]["text"] for m in context["messages"]] == ["Hello from SDK"]

    # List
    sessions = await client.list_sessions()
    assert isinstance(sessions, list)


async def test_sdk_batch_add_messages_and_commit_keep_recent_count(http_client):
    client, _ = http_client

    session_info = await client.create_session()
    session_id = session_info["session_id"]

    batch_result = await client.batch_add_messages(
        session_id,
        [
            {
                "role": "user",
                "peer_id": "sdk-user-1",
                "created_at": "2026-05-01T12:00:00Z",
                "parts": [{"type": "text", "text": "Batch hello"}],
            },
            {
                "role": "assistant",
                "peer_id": "sdk-bot-1",
                "created_at": "2026-05-01T12:00:05Z",
                "parts": [
                    {"type": "text", "text": "Batch answer"},
                    {
                        "type": "context",
                        "uri": "viking://resources/sdk-doc",
                        "context_type": "resource",
                        "abstract": "SDK doc abstract",
                    },
                ],
            },
            {
                "role": "user",
                "content": "Keep me live",
            },
        ],
    )
    assert batch_result["added"] == 3

    pre_commit = await client.get_session_context(session_id)
    assert [m["role"] for m in pre_commit["messages"]] == ["user", "assistant", "user"]
    assert pre_commit["messages"][0]["peer_id"] == "sdk-user-1"
    assert pre_commit["messages"][0]["created_at"] == "2026-05-01T12:00:00Z"
    assert pre_commit["messages"][1]["parts"][1]["type"] == "context"
    assert pre_commit["messages"][1]["parts"][1]["abstract"] == "SDK doc abstract"

    commit_result = await client.commit_session(session_id, keep_recent_count=1)
    task_id = commit_result["task_id"]

    for _ in range(100):
        task = await client.get_task(task_id)
        if task and task["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.1)

    post_commit = await client.get_session_context(session_id)
    assert post_commit["latest_archive_overview"]
    assert [m["role"] for m in post_commit["messages"]] == ["user"]
    assert post_commit["messages"][0]["parts"][0]["text"] == "Keep me live"


async def test_sdk_commit_session_keeps_telemetry_as_second_positional_argument():
    calls = []

    class _FakeHTTP:
        async def post(self, path, json):
            calls.append((path, json))
            return httpx.Response(
                200,
                json={"status": "success", "result": {"task_id": "task-1"}},
            )

    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    client._http = _FakeHTTP()

    result = await client.commit_session("s1", True)

    assert result == {"task_id": "task-1"}
    assert calls == [
        (
            "/api/v1/sessions/s1/commit",
            {"keep_recent_count": 0, "telemetry": True},
        )
    ]


async def test_sdk_get_session_archive(http_client):
    client, _ = http_client

    session_info = await client.create_session()
    session_id = session_info["session_id"]

    await client.add_message(session_id, "user", "Archive me")
    commit_result = await client.commit_session(session_id)
    task_id = commit_result["task_id"]

    for _ in range(100):
        task = await client.get_task(task_id)
        if task and task["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.1)

    archive = await client.get_session_archive(session_id, "archive_001")
    assert archive["archive_id"] == "archive_001"
    assert archive["overview"]
    assert archive["abstract"]
    assert [m["parts"][0]["text"] for m in archive["messages"]] == ["Archive me"]


async def test_sdk_commit_raises_failed_precondition_after_failed_archive(http_client):
    client, svc = http_client

    session_info = await client.create_session()
    session_id = session_info["session_id"]

    async def failing_extract(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("synthetic extraction failure")

    svc.session_compressor.extract_long_term_memories = failing_extract

    await client.add_message(session_id, "user", "First round")
    commit_result = await client.commit_session(session_id)
    task_id = commit_result["task_id"]

    for _ in range(100):
        task = await client.get_task(task_id)
        if task and task["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.1)

    await client.add_message(session_id, "user", "Second round")
    with pytest.raises(FailedPreconditionError, match="unresolved failed archive"):
        await client.commit_session(session_id)


# ===================================================================
# Search
# ===================================================================


async def test_sdk_find(http_client):
    client, _ = http_client
    # Add a resource first
    f = TEST_TMP_DIR / "sdk_search.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SAMPLE_MD_CONTENT)
    await client.add_resource(path=str(f), reason="search test", wait=True)

    result = await client.find(query="sample document", limit=5)
    assert hasattr(result, "resources")
    assert hasattr(result, "total")


async def test_sdk_find_telemetry(http_client):
    client, _ = http_client
    f = TEST_TMP_DIR / "sdk_search_telemetry.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SAMPLE_MD_CONTENT)
    await client.add_resource(
        path=str(f), reason="telemetry search test", wait=True, telemetry=True
    )

    result = await client.find(query="sample document", limit=5, telemetry=True)
    assert not hasattr(result, "telemetry")


async def test_sdk_find_summary_only_telemetry(http_client):
    client, _ = http_client
    f = TEST_TMP_DIR / "sdk_search_summary_only.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SAMPLE_MD_CONTENT)
    await client.add_resource(
        path=str(f),
        reason="summary only telemetry search test",
        wait=True,
    )

    result = await client.find(
        query="sample document",
        limit=5,
        telemetry={"summary": True},
    )
    assert not hasattr(result, "telemetry")


# ===================================================================
# Full workflow
# ===================================================================


async def test_sdk_full_workflow(http_client):
    """End-to-end: add resource → wait → find → session → ls → rm."""
    client, _ = http_client

    # Add resource
    f = TEST_TMP_DIR / "sdk_e2e.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SAMPLE_MD_CONTENT)
    result = await client.add_resource(path=str(f), reason="e2e test", wait=True)
    uri = result["root_uri"]

    # Search
    find_result = await client.find(query="sample", limit=3)
    assert find_result.total >= 0

    # List contents (the URI is a directory)
    children = await client.ls(uri, simple=True)
    assert isinstance(children, list)

    # Session
    session_info = await client.create_session()
    sid = session_info["session_id"]
    await client.add_message(sid, "user", "testing e2e")

    # Cleanup
    await client.rm(uri, recursive=True)
