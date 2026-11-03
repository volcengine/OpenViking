# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write endpoint."""

import json
from types import SimpleNamespace

import pytest

from openviking.server.routers import filesystem


async def _first_file_uri(client, root_uri: str) -> str:
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "simple": True, "recursive": True, "output": "original"},
    )
    assert resp.status_code == 200
    children = resp.json().get("result", [])
    assert children
    return children[0]


async def test_write_endpoint_registered(client):
    resp = await client.get("/api/v1/content/write")
    assert resp.status_code == 405


async def test_set_tags_endpoint_registered(client):
    resp = await client.get("/api/v1/fs/attrs/set_tags")
    assert resp.status_code == 405


async def test_fs_attrs_filters_legacy_non_kv_search_tags(monkeypatch):
    class FakeVikingDBManagerProxy:
        def __init__(self, *_args):
            pass

        async def filter(self, **_kwargs):
            return [
                {
                    "level": 2,
                    "search_tags": ["default", "team=search", "bad=", "channel=app"],
                }
            ]

    monkeypatch.setattr(filesystem, "VikingDBManagerProxy", FakeVikingDBManagerProxy)

    tags = await filesystem._tags_attr(
        SimpleNamespace(vikingdb_manager=object()),
        "viking://user/default/memories/events/example.md",
        SimpleNamespace(),
        is_dir=False,
    )

    assert tags == ["team=search", "channel=app"]


async def test_write_rejects_directory_uri(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/write",
        json={"uri": uri, "content": "new content"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_write_allows_existing_semantic_sidecar_but_rejects_relations(client_with_resource):
    client, uri = client_with_resource

    overview_resp = await client.post(
        "/api/v1/content/write",
        json={"uri": f"{uri}/.overview.md", "content": "new content"},
    )
    assert overview_resp.status_code == 200

    malformed_resp = await client.post(
        "/api/v1/content/write",
        json={"uri": f"{uri}/.overview.md", "content": "---\n"},
    )
    assert malformed_resp.status_code == 400
    assert malformed_resp.json()["error"]["code"] == "INVALID_ARGUMENT"

    relations_resp = await client.post(
        "/api/v1/content/write",
        json={"uri": f"{uri}/.relations.json", "content": "new content"},
    )
    assert relations_resp.status_code == 400
    body = relations_resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("title", "replacement", "expected_anchor"),
    [
        ("# Original", "Resolved content.", "Resolved content."),
        ("# Original", "Resolved content.\n\nContinued.", "Resolved content.\n\nContinued."),
        ("# Updated", "Resolved content.", "Conflicting content"),
    ],
    ids=["reliable-anchor", "multiline-anchor", "uncertain-anchor"],
)
async def test_write_replaces_resource_and_preserves_manual_conflict_resolution(
    client_with_resource, title, replacement, expected_anchor
):
    """An explicit resolution stores the edit, retains conflict history, and refreshes tags."""
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    conflict = {"state": "unfixed", "match_text": "Conflicting content"}
    original = (
        "# Original\n\nConflicting content\n\nAdditional context.\n\n<!-- CONFLICTS_CONTENT\n"
        + json.dumps(conflict)
        + "\n-->"
    )
    seeded = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": original,
            "tags": ["has_conflict=true", "conflict_num=1", "team=search"],
            "wait": True,
        },
    )
    assert seeded.status_code == 200, seeded.text
    edited = f"{title}\n\n{replacement}\n\nAdditional context.\n\n"
    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": edited,
            "mode": "replace",
            "wait": True,
            "resolve_conflicts": True,
        },
    )
    assert write_resp.status_code == 200, write_resp.text
    body = write_resp.json()
    assert body["result"]["uri"] == file_uri and body["result"]["mode"] == "replace"
    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    stored = read_resp.json()["result"]
    assert stored.startswith(edited)
    resolution = json.loads(stored.split("<!-- CONFLICTS_CONTENT", 1)[1].split("-->", 1)[0])
    assert resolution["state"] == "fixed" and resolution["fix_time"] > 0
    assert resolution["match_text"] == expected_anchor
    assert resolution["fix_plan"] == (
        "用户手动解决内容冲突，修正后的内容如正文所示，后续更新的时候不能修改这部分内容"
    )
    assert resolution["resolution_basis"] == "人工修复"
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": file_uri})
    assert {"has_conflict=false", "conflict_num=0", "team=search"} <= set(
        attrs.json()["result"]["attrs"]["tags"]
    )


async def test_write_appends_existing_resource_file(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    original = (await client.get("/api/v1/content/read", params={"uri": file_uri})).json()["result"]

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "\n\nAppended section.",
            "mode": "append",
            "wait": True,
        },
    )
    assert write_resp.status_code == 200

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == original + "\n\nAppended section."


async def test_write_without_wait_is_immediately_readable(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    original = (await client.get("/api/v1/content/read", params={"uri": file_uri})).json()["result"]

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "\nImmediate append.",
            "mode": "append",
            "wait": False,
        },
    )
    assert write_resp.status_code == 200
    body = write_resp.json()
    assert body["result"]["semantic_status"] == "queued"
    assert body["result"]["vector_status"] == "queued"
    task_id = body["result"]["task_id"]

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == original + "\nImmediate append."

    wait_resp = await client.post("/api/v1/system/wait", json={})
    assert wait_resp.status_code == 200
    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["result"]["status"] == "completed"


async def test_write_agent_skill_completes_without_lock_self_blocking(client):
    uri = "viking://agent/skills/content-write-lock/SKILL.md"
    content = """---
name: content-write-lock
description: Verify direct Agent Skill writes complete.
---

# Content Write Lock

Use this skill to verify lock handoff behavior.
"""

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": content,
            "mode": "create",
            "wait": True,
        },
    )

    assert write_resp.status_code == 200
    body = write_resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"] == uri
    assert body["result"]["semantic_status"] == "complete"
    assert body["result"]["vector_status"] == "complete"


@pytest.mark.asyncio
async def test_write_missing_uri_validation(client):
    resp = await client.post("/api/v1/content/write", json={"content": "missing uri"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_write_rejects_removed_semantic_flags(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)

    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "updated",
            "regenerate_semantics": False,
            "revectorize": False,
        },
    )

    assert resp.status_code == 400


async def test_api_create_mode_new_file_success(client):
    """Test create mode with a new file."""
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/default/memories/new_file.md",
            "content": "new content",
            "mode": "create",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["mode"] == "create"


async def test_api_create_mode_write_then_read(client):
    """Create a new file then read it back — verify content roundtrips."""
    uri = "viking://user/default/memories/create_readback_test.md"

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "# Hello\n\nWrite-then-read verification.",
            "mode": "create",
            "wait": True,
        },
    )
    assert write_resp.status_code == 200

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "# Hello\n\nWrite-then-read verification."


async def test_api_create_mode_existing_file_overwrites(client_with_resource):
    """create is an upsert: writing an existing file overwrites it, no 409."""
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)

    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "# Overwritten\n\nvia create.",
            "mode": "create",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["mode"] == "create"

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "# Overwritten\n\nvia create."


async def test_api_create_mode_invalid_extension_400(client):
    """Test create mode with .exe extension should return 400."""
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/default/memories/test.exe",
            "content": "malicious content",
            "mode": "create",
            "wait": True,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "extension" in body["error"]["message"].lower()


async def test_api_create_mode_empty_content_success(client):
    """Test create mode with empty content should succeed."""
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/default/memories/empty.md",
            "content": "",
            "mode": "create",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["mode"] == "create"


async def test_api_create_mode_regression_replace_unchanged(client_with_resource):
    """Test replace mode still works (regression test)."""
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "# Updated\n\nFresh content.",
            "mode": "replace",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["mode"] == "replace"

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "# Updated\n\nFresh content."


async def test_set_tags_omitted_tags_is_noop(client, service, monkeypatch):
    captured = {}

    async def fake_set_tags(
        *,
        uri,
        tags,
        ctx,
        mode="replace",
        recursive=False,
    ):
        del ctx
        captured["uri"] = uri
        captured["tags"] = tags
        captured["mode"] = mode
        captured["recursive"] = recursive
        return {"uri": uri, "tags": tags, "mode": mode}

    monkeypatch.setattr(service.fs, "set_tags", fake_set_tags)

    resp = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={"uri": "viking://resources/demo/file.md"},
    )

    assert resp.status_code == 200
    assert captured == {
        "uri": "viking://resources/demo/file.md",
        "tags": [],
        "mode": "replace",
        "recursive": False,
    }


async def test_set_tags_clear_allows_omitted_tags(client, service, monkeypatch):
    captured = {}

    async def fake_set_tags(
        *,
        uri,
        tags,
        ctx,
        mode="replace",
        recursive=False,
    ):
        del ctx
        captured["uri"] = uri
        captured["tags"] = tags
        captured["mode"] = mode
        captured["recursive"] = recursive
        return {"uri": uri, "tags": tags, "mode": mode}

    monkeypatch.setattr(service.fs, "set_tags", fake_set_tags)

    resp = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={"uri": "viking://resources/demo/file.md", "mode": "clear"},
    )

    assert resp.status_code == 200
    assert captured == {
        "uri": "viking://resources/demo/file.md",
        "tags": [],
        "mode": "clear",
        "recursive": False,
    }


async def test_set_tags_passes_tags_to_service(client, service, monkeypatch):
    captured = {}

    async def fake_set_tags(
        *,
        uri,
        tags,
        ctx,
        mode="replace",
        recursive=False,
    ):
        del ctx
        captured["uri"] = uri
        captured["tags"] = tags
        captured["mode"] = mode
        captured["recursive"] = recursive
        return {"uri": uri, "tags": tags}

    monkeypatch.setattr(service.fs, "set_tags", fake_set_tags)

    resp = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={
            "uri": "viking://resources/demo/file.md",
            "tags": ["a", "b"],
            "mode": "append",
            "recursive": True,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured == {
        "uri": "viking://resources/demo/file.md",
        "tags": ["a", "b"],
        "mode": "append",
        "recursive": True,
    }


async def test_set_tags_rejects_wait_and_timeout_fields(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    resp = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={
            "uri": file_uri,
            "tags": ["team=search"],
            "wait": True,
            "timeout": 3,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"


async def test_set_tags_discards_invalid_kv_tag(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    resp = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={"uri": file_uri, "tags": ["project-a", "team=search"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["tags"] == ["team=search"]
