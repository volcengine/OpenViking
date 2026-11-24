# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""SDK tests using AsyncHTTPClient against a real uvicorn server."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from openviking.server.identity import RequestContext, Role
from openviking.utils.time_utils import format_iso8601
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.exceptions import ConflictError, NotFoundError, ProcessingError
from openviking_cli.session.user_id import UserIdentifier
from tests.server.conftest import SAMPLE_MD_CONTENT
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


async def test_sdk_add_resource(http_client, tmp_path):
    client, service = http_client
    f = tmp_path / "sdk_sample.md"
    f.write_text(SAMPLE_MD_CONTENT)

    result = await client.add_resource(path=str(f), wait=True)
    assert result["root_uri"].startswith("viking://resources/")
    creator = RequestContext(
        user=UserIdentifier("sdk_test_account", "sdk_test_user"),
        role=Role.ADMIN,
    )
    acl = await service.fs.get_acl(result["root_uri"], ctx=creator)
    assert acl["acl_mode"] == "inherit"
    assert acl["direct_entries"] == []
    content = await service.fs.read(result["root_uri"] + "/sdk_sample.md", ctx=creator)
    assert content == SAMPLE_MD_CONTENT


@pytest.mark.usefixtures("public_resource_dns")
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


async def test_sdk_add_skill_from_local_file(http_client, tmp_path):
    client, _ = http_client
    f = tmp_path / "sdk_skill.md"
    f.write_text(
        """---
name: sdk-skill
description: SDK localhost upload test
---

# SDK Skill
"""
    )

    result = await client.add_skill(data=str(f), wait=True)
    root = "viking://user/sdk_test_user/skills/sdk-skill"
    assert result["root_uri"] == result["uri"] == root
    assert "# SDK Skill" in await client.read(f"{root}/SKILL.md")


async def test_sdk_import_ovpack_from_local_file(http_client, tmp_path):
    client, _ = http_client
    f = tmp_path / "sdk_import.ovpack"
    f.write_bytes(build_ovpack_bytes())

    parent = "viking://resources/imported"
    uri = await client.import_ovpack(str(f), parent=parent)
    assert uri == f"{parent}/pkg"
    assert await client.read(f"{uri}/content.md") == "# Demo\n"

    f.write_bytes(build_ovpack_bytes(files={"content.md": b"# Replacement\n"}))
    assert await client.import_ovpack(str(f), parent=parent, on_conflict="skip") == uri
    assert await client.read(f"{uri}/content.md") == "# Demo\n"


# ===================================================================
# Filesystem
# ===================================================================


async def test_sdk_mkdir_and_ls(http_client):
    client, _ = http_client
    uri = "viking://resources/sdk_dir"
    nested = f"{uri}/nested"

    await client.mkdir(uri)
    await client.mkdir(nested)

    assert await client.abstract(uri) == "# sdk_dir"
    assert uri in {entry["uri"] for entry in await client.ls("viking://resources")}
    assert nested in {entry["uri"] for entry in await client.tree(uri)}


async def test_sdk_mkdir_with_description_sets_abstract(http_client):
    client, _ = http_client
    uri = "viking://resources/sdk_dir_desc/"
    description = "SDK directory description"

    await client.mkdir(uri, description=description)

    assert await client.abstract(uri) == description

    with pytest.raises(ConflictError):
        await client.mkdir(uri)
    assert await client.abstract(uri) == description


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
    assert session_id in {entry["session_id"] for entry in sessions}

    await client.delete_session(session_id)
    assert session_id not in {entry["session_id"] for entry in await client.list_sessions()}


async def test_sdk_batch_add_messages_and_commit_keep_recent_count(http_client):
    client, _ = http_client

    session_info = await client.create_session(
        options={"memory_policy": {"self": {"enabled": False}, "peer": {"enabled": False}}}
    )
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

    assert task is not None and task["status"] == "completed", task
    archive = await client.get_session_archive(session_id, "archive_001")
    assert archive["archive_id"] == "archive_001"
    assert [m["parts"][0]["text"] for m in archive["messages"]] == ["Batch hello", "Batch answer"]
    post_commit = await client.get_session_context(session_id)
    assert [m["role"] for m in post_commit["messages"]] == ["user"]
    assert post_commit["messages"][0]["parts"][0]["text"] == "Keep me live"


# ===================================================================
# Search
# ===================================================================


async def test_sdk_find_and_search_use_tags(http_client):
    client, _ = http_client
    tagged = "viking://resources/tagged.md"
    untagged = "viking://resources/untagged.md"
    for uri in (tagged, untagged):
        await client.write(uri, "Searchable rollback documentation.", mode="create", wait=True)

    await client.set_tags(tagged, ["team=search"])
    for retrieve in (client.find, client.search):
        baseline = await retrieve("rollback", target_uri="viking://resources", options={"level": 2})
        assert {hit["uri"] for hit in baseline["resources"]} == {tagged, untagged}

        result = await retrieve(
            "rollback",
            target_uri="viking://resources",
            options={"level": 2, "tags": ["team=search"], "telemetry": True},
        )
        assert [hit["uri"] for hit in result["resources"]] == [tagged]
        assert result["total"] == 1
        assert "telemetry" not in result


async def test_sdk_search_time_filters_use_requested_field_and_default_updated_at(http_client):
    client, svc = http_client
    target = "viking://resources/email"
    new, updated, old = (f"{target}/{name}.md" for name in ("new", "updated", "old"))
    now = datetime.now(timezone.utc)
    recent = format_iso8601(now - timedelta(hours=1))
    stale = format_iso8601(now - timedelta(days=10))
    ctx = RequestContext(
        user=UserIdentifier("sdk_test_account", "sdk_test_user"), role=Role(Role.ADMIN)
    )
    embedder = svc.viking_fs.query_embedder
    vector = embedder.embed("invoice").dense_vector
    for uri, created, modified in (
        (new, recent, recent),
        (updated, stale, recent),
        (old, stale, stale),
    ):
        await svc.vikingdb_manager.upsert(
            {
                "uri": uri,
                "parent_uri": target,
                "is_leaf": True,
                "abstract": "Invoice follow-up",
                "context_type": "resource",
                "created_at": created,
                "updated_at": modified,
                "vector": vector,
                "account_id": ctx.account_id,
                "level": 2,
            },
            ctx=ctx,
        )

    found = await client.find(
        "invoice", target_uri=target, options={"since": "2d", "time_field": "created_at"}
    )
    assert {hit["uri"] for hit in found["resources"]} == {new}

    recent_hits = await client.search("invoice", target_uri=target, options={"since": "2h"})
    assert {hit["uri"] for hit in recent_hits["resources"]} == {new, updated}

    old_hits = await client.search("invoice", target_uri=target, options={"until": "7d"})
    assert {hit["uri"] for hit in old_hits["resources"]} == {old}


# ===================================================================
# Full workflow
# ===================================================================


async def test_sdk_full_workflow(http_client, tmp_path):
    """Import, list, search, and remove the same uploaded resource."""
    client, _ = http_client

    f = tmp_path / "sdk_e2e.md"
    f.write_text(SAMPLE_MD_CONTENT)
    result = await client.add_resource(path=str(f), wait=True)
    uri = result["root_uri"]
    file_uri = f"{uri}/{f.name}"

    find_result = await client.find(query="sample", target_uri=uri, limit=1, options={"level": 2})
    assert [hit["uri"] for hit in find_result["resources"]] == [file_uri]
    assert file_uri in await client.ls(uri, simple=True)

    await client.rm(uri, recursive=True)
    with pytest.raises(NotFoundError):
        await client.read(file_uri)
    queue_status = await client.wait_processed()
    assert queue_status["Embedding"]["error_count"] == 0
    found = await client.find("sample", target_uri="viking://resources", options={"level": 2})
    assert file_uri not in {hit["uri"] for hit in found["resources"]}
