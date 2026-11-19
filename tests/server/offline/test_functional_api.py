# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""API contracts that need processing and indexing, but not model quality."""

import pytest

from openviking.core.mcp_converter import mcp_to_skill
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def result(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body
    assert not body.get("error"), body
    return body["result"]


async def write(client, uri, content):
    written = result(
        await client.post(
            "/api/v1/content/write",
            json={"uri": uri, "content": content, "mode": "create", "wait": True},
        )
    )
    assert written["vector_status"] == "complete", written
    assert written["semantic_status"] != "failed", written
    for queue in (written.get("queue_status") or {}).values():
        assert not queue.get("error_count", 0), queue
        assert not queue.get("errors"), queue
    return written


async def read(client, uri):
    return result(await client.get("/api/v1/content/read", params={"uri": uri}))


async def assert_indexed(client, uri, context_type="resources"):
    found = result(
        await client.post(
            "/api/v1/search/find",
            json={
                "query": "",
                "target_uri": uri,
                "filter": {"op": "must", "field": "uri", "conds": [uri]},
                "level": [2],
                "limit": 5,
            },
        )
    )
    assert any(hit["uri"] == uri for hit in found[context_type]), found


@pytest.mark.parametrize("mcp_format", [False, True], ids=["dict", "mcp"])
async def test_add_skill_stores_content_and_indexes_all_levels(client, mcp_format):
    name = "offline_calculator"
    description = "Perform mathematical calculations"
    skill = {
        "name": name,
        "description": description,
        "content": "# Calculator\n\nEvaluate the supplied expression.",
    }
    if mcp_format:
        skill = {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        }
    added = result(await client.post("/api/v1/skills", json={"data": skill, "wait": True}))
    assert added["status"] == "success", added
    expected_name = name.replace("_", "-") if mcp_format else name
    assert added["name"] == expected_name
    root = added["root_uri"]
    assert root.endswith(f"/skills/{expected_name}")
    shown = result(await client.get(f"/api/v1/skills/{expected_name}"))
    assert shown["name"] == expected_name
    assert shown["description"] == description
    expected = mcp_to_skill(skill)["content"] if mcp_format else skill["content"]
    assert expected.strip() in shown["content"]
    assert any(file["name"] == "SKILL.md" for file in shown["files"])
    await assert_indexed(client, f"{root}/SKILL.md", "skills")
    for level in (0, 1):
        found = result(
            await client.post(
                "/api/v1/search/find",
                json={
                    "query": "",
                    "target_uri": root,
                    "filter": {"op": "must", "field": "uri", "conds": [root]},
                    "level": [level],
                    "limit": 5,
                },
            )
        )
        assert any(hit["uri"] == root and hit["level"] == level for hit in found["skills"]), found


async def test_resource_import_persists_content_and_index(offline_resource_api):
    client, root = offline_resource_api
    entries = result(
        await client.get("/api/v1/fs/ls", params={"uri": root, "simple": True, "recursive": True})
    )
    assert entries, entries
    uri = next(uri for uri in entries if uri.endswith("oauth.md"))
    assert "Use OAuth authorization codes" in await read(client, uri)
    await assert_indexed(client, uri)


async def test_resource_delete_removes_files_and_index(offline_resource_api):
    client, root = offline_resource_api
    found = result(
        await client.post(
            "/api/v1/search/find",
            json={"query": "OAuth", "target_uri": root, "level": [2], "limit": 5},
        )
    )
    assert found["resources"], found
    result(await client.request("DELETE", "/api/v1/fs", params={"uri": root, "recursive": True}))
    missing = await client.get("/api/v1/fs/stat", params={"uri": root})
    assert missing.status_code == 404, missing.text
    # Search the parent, so absence is established by index filtering rather
    # than just the deleted target's filesystem existence check.
    after = result(
        await client.post(
            "/api/v1/search/find",
            json={"query": "OAuth", "target_uri": "viking://resources", "level": [2], "limit": 5},
        )
    )
    assert not any(
        hit["uri"] == root or hit["uri"].startswith(root + "/") for hit in after["resources"]
    ), after


@pytest.mark.parametrize("endpoint", ["find", "search"])
@pytest.mark.parametrize("query", ["how to authenticate", "what is OpenViking"])
async def test_basic_retrieval_contract(offline_resource_api, endpoint, query):
    client, root = offline_resource_api
    found = result(
        await client.post(
            f"/api/v1/search/{endpoint}",
            json={"query": query, "target_uri": root, "level": [2], "limit": 5},
        )
    )
    for field in ("resources", "memories", "skills"):
        assert isinstance(found[field], list)
    assert isinstance(found["total"], int)
    assert found["resources"], found
    assert len(found["resources"]) <= 5
    for hit in found["resources"]:
        assert hit["uri"].startswith(root + "/"), hit
        assert hit["level"] == 2
        assert 0 <= hit["score"] <= 1


async def test_copy_file_preserves_source_content_and_index(client):
    source, target = "viking://resources/source.md", "viking://resources/target.md"
    content = "# Copy payload\n\nSource must remain after copying."
    await write(client, source, content)
    await assert_indexed(client, source)
    copied = result(await client.post("/api/v1/fs/cp", json={"from_uri": source, "to_uri": target}))
    assert copied["from"] == source
    assert copied["to"] == target
    assert copied["recursive"] is False
    assert copied["vectors"]["scanned"] > 0, copied
    assert copied["vectors"]["written"] == copied["vectors"]["scanned"], copied
    for uri in (source, target):
        assert await read(client, uri) == content
        await assert_indexed(client, uri)
    result(await client.request("DELETE", "/api/v1/fs", params={"uri": source}))
    assert await read(client, target) == content
    await assert_indexed(client, target)


async def test_copy_overwrites_target_content_and_index(client, service):
    source, target = "viking://resources/source.md", "viking://resources/target.md"
    await write(client, source, "New source content.")
    old_content = "Old target content must be replaced.\n" * 200
    await write(client, target, old_content)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    index = service.viking_fs.vector_store
    before = await index.get_l2_diff_records_by_uris([source, target], ctx=ctx)
    assert set(before) == {source, target}
    assert before[source]["md5"] and before[target]["md5"]
    assert before[source]["md5"] != before[target]["md5"]
    copied = result(await client.post("/api/v1/fs/cp", json={"from_uri": source, "to_uri": target}))
    assert copied["phase"] == "completed"
    for uri in (source, target):
        assert await read(client, uri) == "New source content."
        await assert_indexed(client, uri)
    after = await index.get_l2_diff_records_by_uris([source, target], ctx=ctx)
    assert after[source] == before[source]
    assert after[target] == before[source]
    assert after[target]["md5"] != before[target]["md5"]


async def test_copy_directory_requires_recursive_and_copies_tree(client):
    source, target = "viking://resources/source", "viking://resources/target"
    for uri in (source, source + "/nested", source + "/empty"):
        result(await client.post("/api/v1/fs/mkdir", json={"uri": uri}))
    await write(client, source + "/nested/child.md", "Recursive copy child.")
    refused = await client.post("/api/v1/fs/cp", json={"from_uri": source, "to_uri": target})
    assert refused.status_code == 400, refused.text
    result(await client.post("/api/v1/system/wait", json={}))
    copied = result(
        await client.post(
            "/api/v1/fs/cp",
            json={"from_uri": source, "to_uri": target, "recursive": True},
        )
    )
    assert copied["recursive"] is True
    for root in (source, target):
        assert await read(client, root + "/nested/child.md") == "Recursive copy child."
        result(await client.get("/api/v1/fs/stat", params={"uri": root + "/empty"}))
        await assert_indexed(client, root + "/nested/child.md")


@pytest.mark.parametrize("source_is_dir", [False, True], ids=["file-to-dir", "dir-to-file"])
async def test_copy_rejects_file_directory_conflicts(client, source_is_dir):
    file_uri, directory = "viking://resources/file.md", "viking://resources/directory"
    result(await client.post("/api/v1/fs/mkdir", json={"uri": directory}))
    await write(client, file_uri, "File remains.")
    await write(client, directory + "/child.md", "Directory child remains.")
    source, target = (directory, file_uri) if source_is_dir else (file_uri, directory)
    refused = await client.post(
        "/api/v1/fs/cp",
        json={"from_uri": source, "to_uri": target, "recursive": source_is_dir},
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert await read(client, file_uri) == "File remains."
    assert await read(client, directory + "/child.md") == "Directory child remains."
