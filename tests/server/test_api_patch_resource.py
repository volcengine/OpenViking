# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for PATCH /api/v1/resources endpoint."""

import httpx


async def test_patch_resource_meta(client: httpx.AsyncClient, sample_markdown_file):
    """Add a resource then patch its metadata."""
    add_resp = await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test", "wait": True},
    )
    assert add_resp.status_code == 200
    root_uri = add_resp.json()["result"]["root_uri"]

    patch_resp = await client.patch(
        "/api/v1/resources",
        json={"uri": root_uri, "meta": {"tags": ["important"], "outdated": False}},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["status"] == "ok"
    assert "meta" in body["result"]["updated"]


async def test_patch_resource_abstract(client: httpx.AsyncClient, sample_markdown_file):
    """Patch L0 abstract of a resource."""
    add_resp = await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test", "wait": True},
    )
    assert add_resp.status_code == 200
    root_uri = add_resp.json()["result"]["root_uri"]

    patch_resp = await client.patch(
        "/api/v1/resources",
        json={"uri": root_uri, "abstract": "Custom abstract override"},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert "abstract" in body["result"]["updated"]


async def test_patch_resource_overview(client: httpx.AsyncClient, sample_markdown_file):
    """Patch L1 overview of a resource."""
    add_resp = await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test", "wait": True},
    )
    assert add_resp.status_code == 200
    root_uri = add_resp.json()["result"]["root_uri"]

    patch_resp = await client.patch(
        "/api/v1/resources",
        json={"uri": root_uri, "overview": "# Custom Overview\n\nManual overview content."},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert "overview" in body["result"]["updated"]


async def test_patch_resource_combo(client: httpx.AsyncClient, sample_markdown_file):
    """Patch meta + abstract + overview in a single request."""
    add_resp = await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test", "wait": True},
    )
    assert add_resp.status_code == 200
    root_uri = add_resp.json()["result"]["root_uri"]

    patch_resp = await client.patch(
        "/api/v1/resources",
        json={
            "uri": root_uri,
            "meta": {"reviewed": True},
            "abstract": "Combined abstract",
            "overview": "# Combined Overview\n\nAll fields at once.",
        },
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    result = body["result"]
    assert "meta" in result["updated"] or "meta" in result.get("skipped", [])
    assert "abstract" in result["updated"]
    assert "overview" in result["updated"]


async def test_patch_resource_meta_skipped_when_no_record(
    client: httpx.AsyncClient, sample_markdown_file
):
    """Meta patch on a resource with no VectorDB record should be skipped."""
    add_resp = await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test", "wait": False},
    )
    assert add_resp.status_code == 200
    root_uri = add_resp.json()["result"]["root_uri"]

    # Patch meta immediately (vectorization may not have completed yet)
    patch_resp = await client.patch(
        "/api/v1/resources",
        json={"uri": root_uri, "meta": {"key": "val"}},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    result = body["result"]
    # meta should be either updated or skipped depending on timing
    assert "meta" in result["updated"] or "meta" in result.get("skipped", [])


async def test_patch_resource_meta_after_wait(client: httpx.AsyncClient, sample_markdown_file):
    """Meta patch after wait=True must deterministically land in updated."""
    add_resp = await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test", "wait": True},
    )
    assert add_resp.status_code == 200
    root_uri = add_resp.json()["result"]["root_uri"]

    patch_resp = await client.patch(
        "/api/v1/resources",
        json={"uri": root_uri, "meta": {"verified": True}},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert "meta" in body["result"]["updated"]
    assert "meta" not in body["result"].get("skipped", [])


async def test_patch_resource_requires_at_least_one_field(client: httpx.AsyncClient):
    """PATCH with no update fields should fail validation."""
    resp = await client.patch(
        "/api/v1/resources",
        json={"uri": "viking://resources/nonexistent"},
    )
    assert resp.status_code == 422


async def test_patch_resource_not_found(client: httpx.AsyncClient):
    """PATCH on nonexistent URI should return 404."""
    resp = await client.patch(
        "/api/v1/resources",
        json={"uri": "viking://resources/does_not_exist", "meta": {"key": "val"}},
    )
    assert resp.status_code == 404
