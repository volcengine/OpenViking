# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""SDK tests using AsyncHTTPClient against a real uvicorn server."""

import pytest_asyncio

from openviking_cli.client.http import AsyncHTTPClient
from tests.server.conftest import SAMPLE_MD_CONTENT, TEST_TMP_DIR


@pytest_asyncio.fixture()
async def http_client(running_server):
    """Create an AsyncHTTPClient connected to the running server."""
    port, svc = running_server
    client = AsyncHTTPClient(
        url=f"http://127.0.0.1:{port}",
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
    assert "root_uri" in result
    assert result["root_uri"].startswith("viking://")


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

    # List
    sessions = await client.list_sessions()
    assert isinstance(sessions, list)


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
