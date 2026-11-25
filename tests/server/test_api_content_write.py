# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write endpoint."""

import hashlib
import json

import pytest


async def _first_file_uri(client, root_uri: str) -> str:
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "simple": True, "recursive": True, "output": "original"},
    )
    assert resp.status_code == 200
    children = resp.json().get("result", [])
    assert children
    return children[0]


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
        ("# Updated", "Resolved content.", "Resolved content."),
        ("# Original", "", "# Original"),
        ("", "", None),
        ("", " \n\t", None),
    ],
    ids=["reliable-anchor", "multiline-anchor", "approximate-anchor", "deleted", "empty", "blank"],
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
    edited = (
        replacement
        if expected_anchor is None
        else f"{title}\n\n{replacement}\n\nAdditional context.\n\n"
    )
    write_resp = await client.post(
        "/api/v1/content/write",
        headers={"X-OpenViking-User": "alice"},
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
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": file_uri})
    assert {"has_conflict=false", "conflict_num=0", "team=search"} <= set(
        attrs.json()["result"]["attrs"]["tags"]
    )
    if expected_anchor is None:
        assert stored == edited
        return
    resolution = json.loads(stored.split("<!-- CONFLICTS_CONTENT", 1)[1].split("-->", 1)[0])
    assert resolution["state"] == "fixed" and resolution["fix_time"] > 0
    assert resolution["match_text"] == expected_anchor
    assert resolution["match_text"] in edited
    assert resolution["fix_plan"] == ("用户手动解决内容冲突，修正后的内容如正文所示")
    assert resolution["resolution_basis"] == "人工修复"
    assert resolution["resolved_by"] == "alice"
    assert set(resolution) == set(conflict) | {
        "fix_time",
        "fix_plan",
        "resolution_basis",
        "resolved_by",
    }


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


async def test_api_create_overwrites_the_same_file_and_updates_persisted_tags(client):
    uri = "viking://resources/write-contract/page.md"

    for options, text, tags in (
        (
            {"mode": "create", "tags": ["Team=Search"]},
            "first version",
            {"team=search"},
        ),
        (
            {"mode": "create", "tags": ["env=prod"], "tag_mode": "append"},
            "replacement",
            {"env=prod", "team=search"},
        ),
        ({"mode": "replace"}, "plain replace", {"env=prod", "team=search"}),
        ({"mode": "append", "tag_mode": "clear"}, "\naddition", set()),
    ):
        written = await client.post(
            "/api/v1/content/write",
            json={"uri": uri, "content": text, "wait": True, **options},
        )
        assert written.status_code == 200, written.text
        assert written.json()["result"]["mode"] == options["mode"]
        result = await client.get("/api/v1/content/read", params={"uri": uri})
        assert result.status_code == 200, result.text
        assert result.json()["result"] == (
            "plain replace\naddition" if options["mode"] == "append" else text
        )
        attrs = await client.get("/api/v1/fs/attrs", params={"uri": uri})
        assert attrs.status_code == 200, attrs.text
        assert set(attrs.json()["result"]["attrs"]["tags"]) == tags


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


async def test_set_tags_omitted_tags_is_noop(client_with_resource):
    client, root = client_with_resource
    file_uri = await _first_file_uri(client, root)
    tags = {"team=search", "env=prod"}
    seeded = await client.post(
        "/api/v1/fs/attrs/set_tags", json={"uri": file_uri, "tags": ["team=search"]}
    )
    assert seeded.status_code == 200, seeded.text

    appended = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={"uri": root, "tags": ["env=prod"], "mode": "append", "recursive": True},
    )
    assert appended.status_code == 200, appended.text
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": file_uri})
    assert set(attrs.json()["result"]["attrs"]["tags"]) == tags

    omitted = await client.post("/api/v1/fs/attrs/set_tags", json={"uri": file_uri})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["result"]["tags_updated"] is False
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": file_uri})
    assert set(attrs.json()["result"]["attrs"]["tags"]) == tags

    cleared = await client.post(
        "/api/v1/fs/attrs/set_tags", json={"uri": file_uri, "mode": "clear"}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["result"]["tags_updated"] is True
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": file_uri})
    assert attrs.json()["result"]["attrs"]["tags"] == []


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


async def test_tag_updates_preserve_content_and_isolate_batch_failures(
    client, service, app, monkeypatch
):
    from openviking.server.auth import get_request_context
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=service.user, role=Role.USER)
    foreign_ctx = RequestContext(user=UserIdentifier(ctx.account_id, "other_user"), role=Role.USER)
    root = f"viking://user/{ctx.user.user_id}/resources/tag-contract"
    file_uri = f"{root}/a.md"
    unindexed = f"{root}/unindexed.md"
    missing = f"{root}/missing.md"
    foreign = "viking://user/other_user/resources/tag-contract/private.md"
    for uri, owner in [(file_uri, ctx), (unindexed, ctx), (foreign, foreign_ctx)]:
        await service.viking_fs.write_file(uri, "original content", ctx=owner)
    timestamp = get_current_timestamp()
    record_ids = []
    for uri, level, owner in [
        (file_uri, 2, ctx),
        (root, 0, ctx),
        (root, 1, ctx),
        (foreign, 2, foreign_ctx),
    ]:
        record_id = vector_record_id(ctx.account_id, uri, level)
        record_ids.append(record_id)
        await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "context_type": "resource",
                "level": level,
                "abstract": "original summary",
                "created_at": timestamp,
                "updated_at": timestamp,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
                "search_tags": ["team=search", "used_count=5", "last_used_at=123"]
                + (["default", "bad="] if uri == file_uri else []),
            },
            ctx=owner,
        )
    root_ctx = RequestContext(user=service.user, role=Role.ROOT)
    before = await service.vikingdb_manager.get_strict(record_ids, ctx=root_ctx)
    monkeypatch.setitem(app.dependency_overrides, get_request_context, lambda: ctx)
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": file_uri})
    assert attrs.status_code == 200, attrs.text
    assert set(attrs.json()["result"]["attrs"]["tags"]) == {
        "team=search",
        "used_count=5",
        "last_used_at=123",
    }

    response = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={"uri": file_uri, "tags": ["project-a", "team=search"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["tags"] == ["team=search"]

    response = await client.post(
        "/api/v1/content/set_tags",
        json={
            "uri": file_uri,
            "tags": ["used_count=0", "used_count+=1", "used_count+=1", "last_used_at=123"],
            "mode": "append",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["success_count"] == 1

    response = await client.post(
        "/api/v1/content/batch_set_tags",
        json={
            "uris": [file_uri, foreign, root, missing, file_uri + "/", unindexed, "invalid"],
            "tags": ["used_count+=1"],
            "mode": "append",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [item["uri"] for item in result["items"]] == [file_uri, root, unindexed]
    assert [item["success_count"] for item in result["items"]] == [1, 2, 0]
    assert result["items"][-1]["tags_updated"] is False
    assert [(item["uri"], item["error"]["code"]) for item in result["failed"]] == [
        (foreign, "PERMISSION_DENIED"),
        (missing, "NOT_FOUND"),
        ("invalid", "INVALID_URI"),
    ]
    after = await service.vikingdb_manager.get_strict(record_ids, ctx=root_ctx)
    for record, original in zip(after, before, strict=True):
        if record["uri"] == foreign:
            assert record == original
        else:
            count = 3 if record["uri"] == file_uri else 6
            assert set(record["search_tags"]) == {
                "team=search",
                f"used_count={count}",
                "last_used_at=123",
            }
            assert {key: value for key, value in record.items() if key != "search_tags"} == {
                key: value for key, value in original.items() if key != "search_tags"
            }
    for uri, count in [(file_uri, 3), (root, 6)]:
        attrs = await client.get("/api/v1/fs/attrs", params={"uri": uri})
        assert attrs.status_code == 200, attrs.text
        assert set(attrs.json()["result"]["attrs"]["tags"]) == {
            "team=search",
            f"used_count={count}",
            "last_used_at=123",
        }

    rejected = await client.post(
        "/api/v1/content/set_tags",
        json={"uri": file_uri, "tags": ["used_count+=NaN"], "mode": "append"},
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert await service.vikingdb_manager.get_strict(record_ids, ctx=root_ctx) == after
    for uri, owner in [(file_uri, ctx), (unindexed, ctx), (foreign, foreign_ctx)]:
        assert await service.viking_fs.read_file(uri, ctx=owner) == "original content"


@pytest.mark.parametrize("expected", ["A" * 64, "a" * 63])
async def test_write_revision_rejects_invalid_http_digest(client, expected):
    response = await client.post(
        "/api/v1/content/write",
        json={"uri": "viking://resources/page.md", "content": "new", "expected_sha256": expected},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_write_revision_http_conflict_preserves_content_and_tags(client_with_resource):
    client, root = client_with_resource
    uri = await _first_file_uri(client, root)
    current = await client.get("/api/v1/content/download", params={"uri": uri})
    assert current.status_code == 200
    expected = hashlib.sha256(current.content).hexdigest()
    write = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "first",
            "expected_sha256": expected,
            "tags": ["team=original"],
            "wait": True,
        },
    )
    assert write.status_code == 200, write.text
    stale = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "stale",
            "expected_sha256": expected,
            "tags": ["team=stale"],
            "use_snapshot": True,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "CONFLICT"
    current = await client.get("/api/v1/content/download", params={"uri": uri})
    assert current.content == b"first"
    attrs = await client.get("/api/v1/fs/attrs", params={"uri": uri})
    assert "team=original" in attrs.json()["result"]["attrs"]["tags"]
    assert "team=stale" not in attrs.json()["result"]["attrs"]["tags"]


async def test_write_revision_http_requires_existing_file(client):
    response = await client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://resources/missing.md",
            "content": "new",
            "mode": "create",
            "expected_sha256": hashlib.sha256(b"").hexdigest(),
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFLICT"
