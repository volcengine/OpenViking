# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx


async def test_privacy_config_api_roundtrip(client: httpx.AsyncClient):
    upsert = await client.post(
        "/api/v1/privacy-configs/skill/demo-skill",
        json={
            "values": {"api_key": "secret-1"},
            "change_reason": "seed",
        },
    )
    assert upsert.status_code == 200
    body = upsert.json()
    assert body["status"] == "ok"
    assert body["result"]["version"] == 1

    get_current = await client.get("/api/v1/privacy-configs/skill/demo-skill")
    assert get_current.status_code == 200
    current_body = get_current.json()
    assert current_body["result"]["current"]["values"]["api_key"] == "secret-1"
    assert current_body["result"]["meta"]["active_version"] == 1

    second = await client.post(
        "/api/v1/privacy-configs/skill/demo-skill",
        json={
            "values": {"api_key": "secret-2"},
            "change_reason": "rotate",
        },
    )
    assert second.status_code == 200
    assert second.json()["result"]["version"] == 2

    versions = await client.get("/api/v1/privacy-configs/skill/demo-skill/versions")
    assert versions.status_code == 200
    assert versions.json()["result"] == [1, 2]

    activate = await client.post(
        "/api/v1/privacy-configs/skill/demo-skill/activate",
        json={"version": 1},
    )
    assert activate.status_code == 200
    assert activate.json()["result"]["version"] == 1

    final_current = await client.get("/api/v1/privacy-configs/skill/demo-skill")
    assert final_current.json()["result"]["current"]["values"]["api_key"] == "secret-1"


async def test_privacy_config_update_allows_new_keys(client: httpx.AsyncClient):
    create = await client.post(
        "/api/v1/privacy-configs/skill/demo-skill-unknown-key",
        json={
            "values": {"api_key": "secret-1"},
            "change_reason": "seed",
        },
    )
    assert create.status_code == 200

    update = await client.post(
        "/api/v1/privacy-configs/skill/demo-skill-unknown-key",
        json={
            "values": {"api_key": "secret-2", "new_key": "x"},
            "change_reason": "extend-keys",
        },
    )
    assert update.status_code == 200
    body = update.json()
    assert body["status"] == "ok"
    assert body["result"]["values"]["api_key"] == "secret-2"
    assert body["result"]["values"]["new_key"] == "x"


async def test_privacy_config_get_version_not_found(client: httpx.AsyncClient):
    create = await client.post(
        "/api/v1/privacy-configs/skill/demo-skill-version-not-found",
        json={
            "values": {"api_key": "secret-1"},
            "change_reason": "seed",
        },
    )
    assert create.status_code == 200

    missing = await client.get(
        "/api/v1/privacy-configs/skill/demo-skill-version-not-found/versions/10"
    )
    assert missing.status_code == 404
    body = missing.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"


async def test_privacy_config_target_not_found(client: httpx.AsyncClient):
    missing_target = "demo-skill-target-not-found"

    detail = await client.get(f"/api/v1/privacy-configs/skill/{missing_target}")
    assert detail.status_code == 404
    detail_body = detail.json()
    assert detail_body["status"] == "error"
    assert detail_body["error"]["code"] == "NOT_FOUND"

    versions = await client.get(f"/api/v1/privacy-configs/skill/{missing_target}/versions")
    assert versions.status_code == 404
    versions_body = versions.json()
    assert versions_body["status"] == "error"
    assert versions_body["error"]["code"] == "NOT_FOUND"

    version_detail = await client.get(f"/api/v1/privacy-configs/skill/{missing_target}/versions/1")
    assert version_detail.status_code == 404
    version_detail_body = version_detail.json()
    assert version_detail_body["status"] == "error"
    assert version_detail_body["error"]["code"] == "NOT_FOUND"

    activate = await client.post(
        f"/api/v1/privacy-configs/skill/{missing_target}/activate",
        json={"version": 1},
    )
    assert activate.status_code == 404
    activate_body = activate.json()
    assert activate_body["status"] == "error"
    assert activate_body["error"]["code"] == "NOT_FOUND"
