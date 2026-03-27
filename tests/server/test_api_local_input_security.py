# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Security tests for HTTP server local input handling."""

import io
import zipfile

import httpx


async def test_add_skill_accepts_temp_uploaded_file(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    skill_file = upload_temp_dir / "skill.md"
    skill_file.write_text(
        """---
name: uploaded-skill
description: temp uploaded skill
---

# Uploaded Skill
"""
    )

    resp = await client.post(
        "/api/v1/skills",
        json={"temp_file_id": skill_file.name, "wait": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"].startswith("viking://agent/skills/")


async def test_add_skill_rejects_direct_local_path(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={"data": "/app/ov.conf"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_skill_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={"temp_path": "upload_skill.md"},
    )
    assert resp.status_code == 422


async def test_add_skill_accepts_raw_skill_content(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/skills",
        json={
            "data": """---
name: inline-skill
description: inline
---

# Inline Skill
"""
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"].startswith("viking://agent/skills/")


def _build_ovpack_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("pkg/_._meta.json", '{"uri": "viking://resources/pkg"}')
        zf.writestr("pkg/content.md", "# Demo\n")
    return buffer.getvalue()


async def test_import_ovpack_accepts_temp_uploaded_file(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    ovpack_file = upload_temp_dir / "demo.ovpack"
    ovpack_file.write_bytes(_build_ovpack_bytes())

    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": ovpack_file.name,
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"].startswith("viking://resources/imported/")


async def test_import_ovpack_rejects_direct_file_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "file_path": "/tmp/demo.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 422


async def test_import_ovpack_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_path": "upload_pack.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 422


async def test_import_ovpack_rejects_forged_temp_file_id(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    outside_file = upload_temp_dir.parent / "outside.ovpack"
    outside_file.write_bytes(_build_ovpack_bytes())

    resp = await client.post(
        "/api/v1/pack/import",
        json={
            "temp_file_id": "../outside.ovpack",
            "parent": "viking://resources/imported",
            "vectorize": False,
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_add_resource_rejects_legacy_temp_path_field(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"temp_path": "upload_resource.md", "reason": "legacy field"},
    )
    assert resp.status_code == 422
