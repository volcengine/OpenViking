# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for relations endpoints: get relations, link, unlink."""


async def test_get_relations_empty(client_with_resource):
    client, uri = client_with_resource
    resp = await client.get("/api/v1/relations", params={"uri": uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)


async def test_link_and_get_relations(client_with_resource, upload_temp_dir):
    client, uri = client_with_resource
    # Create a second resource to link to
    from tests.server.conftest import SAMPLE_MD_CONTENT

    f2 = upload_temp_dir / "link_target.md"
    f2.write_text(SAMPLE_MD_CONTENT)
    add_resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": f2.name, "reason": "link target", "wait": True},
    )
    target_uri = add_resp.json()["result"]["root_uri"]

    # Create link
    resp = await client.post(
        "/api/v1/relations/link",
        json={
            "from_uri": uri,
            "to_uris": target_uri,
            "reason": "test link",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Verify link exists
    resp = await client.get("/api/v1/relations", params={"uri": uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["result"]) > 0


async def test_unlink(client_with_resource, upload_temp_dir):
    client, uri = client_with_resource
    from tests.server.conftest import SAMPLE_MD_CONTENT

    f2 = upload_temp_dir / "unlink_target.md"
    f2.write_text(SAMPLE_MD_CONTENT)
    add_resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": f2.name, "reason": "unlink target", "wait": True},
    )
    target_uri = add_resp.json()["result"]["root_uri"]

    # Link then unlink
    await client.post(
        "/api/v1/relations/link",
        json={"from_uri": uri, "to_uris": target_uri, "reason": "temp"},
    )
    resp = await client.request(
        "DELETE",
        "/api/v1/relations/link",
        json={"from_uri": uri, "to_uri": target_uri},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_link_multiple_targets(client_with_resource, upload_temp_dir):
    client, uri = client_with_resource
    from tests.server.conftest import SAMPLE_MD_CONTENT

    targets = []
    for i in range(2):
        f = upload_temp_dir / f"multi_target_{i}.md"
        f.write_text(SAMPLE_MD_CONTENT)
        add_resp = await client.post(
            "/api/v1/resources",
            json={"temp_file_id": f.name, "reason": "multi", "wait": True},
        )
        targets.append(add_resp.json()["result"]["root_uri"])

    resp = await client.post(
        "/api/v1/relations/link",
        json={"from_uri": uri, "to_uris": targets, "reason": "multi link"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_link_from_file_uri(client_with_resource, upload_temp_dir):
    """Link from a file URI (not a directory) should not fail with ENOTDIR.

    Regression test for #3067: ``ov link`` from a file resource was failing
    with "Not a directory (os error 20)" because the relation table was
    stored at ``{file_path}/.relations.json``.
    """
    client, uri = client_with_resource
    from tests.server.conftest import SAMPLE_MD_CONTENT

    # List the resource directory to find a file URI
    ls_resp = await client.get("/api/v1/fs/ls", params={"uri": uri, "simple": True})
    assert ls_resp.status_code == 200
    ls_data = ls_resp.json()
    file_list = ls_data.get("result", [])
    # Find a file entry (not a directory)
    file_uri = None
    for entry in file_list:
        if isinstance(entry, str):
            file_uri = entry if entry.startswith("viking://") else f"{uri}/{entry}"
            break
        elif isinstance(entry, dict):
            name = entry.get("name", "")
            if name and not entry.get("isDir", False) and not name.startswith("."):
                file_uri = entry.get("uri") or f"{uri}/{name}"
                break
    assert file_uri is not None, f"No file found in resource {uri}, ls result: {file_list}"

    # Create a second resource as link target
    f2 = upload_temp_dir / "file_link_target.md"
    f2.write_text(SAMPLE_MD_CONTENT)
    add_resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": f2.name, "reason": "file link target", "wait": True},
    )
    target_uri = add_resp.json()["result"]["root_uri"]

    # Create link from file URI — this used to fail with ENOTDIR
    resp = await client.post(
        "/api/v1/relations/link",
        json={
            "from_uri": file_uri,
            "to_uris": target_uri,
            "reason": "file source link",
        },
    )
    assert resp.status_code == 200, (
        f"Link from file URI should succeed, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["status"] == "ok"

    # Verify link exists
    resp = await client.get("/api/v1/relations", params={"uri": file_uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["result"]) > 0, "Relation should exist after linking from file URI"

    # Unlink should also work
    resp = await client.request(
        "DELETE",
        "/api/v1/relations/link",
        json={"from_uri": file_uri, "to_uri": target_uri},
    )
    assert resp.status_code == 200, (
        f"Unlink from file URI should succeed, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["status"] == "ok"
