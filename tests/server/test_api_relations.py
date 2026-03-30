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
