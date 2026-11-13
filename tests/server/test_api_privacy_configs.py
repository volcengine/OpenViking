# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx

from openviking.privacy.reference import build_privacy_field_reference
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


async def test_privacy_tree_lists_current_fields_without_values(client: httpx.AsyncClient, service):
    created = await client.post(
        "/api/v1/privacy-configs/credential/github",
        json={"values": {"pat": "historical-secret-marker", "old_field": "old-value-marker"}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]
    for category, target, values in [
        ("credential", "github", {"pat": "active-secret-marker", "region": "", "unset": None}),
        ("credential", "flags", {"disabled": False, "retries": 0}),
        ("skill", "demo", {"api/key": "skill-secret-marker"}),
    ]:
        response = await client.post(
            f"/api/v1/privacy-configs/{category}/{target}", json={"values": values}
        )
        assert response.status_code == 200, response.text

    ctx = RequestContext(user=UserIdentifier(service.user.account_id, owner), role=Role.USER)
    await service.viking_fs.mkdir(f"viking://user/{owner}/privacy/credential/lock-only", ctx=ctx)
    other_ctx = RequestContext(
        user=UserIdentifier(service.user.account_id, "other-owner"), role=Role.USER
    )
    await service.privacy_configs.upsert(
        other_ctx, "credential", "other-owner-target", {"token": "other-owner-secret-marker"}
    )
    response = await client.get("/api/v1/privacy-configs/tree")
    assert response.status_code == 200, response.text
    categories = response.json()["result"]
    assert categories["owner_user_id"] == owner
    assert [node["category"] for node in categories["categories"]] == ["credential", "skill"]
    credential, skill = categories["categories"]
    assert [node["target_key"] for node in credential["targets"]] == ["flags", "github"]
    github = credential["targets"][1]
    assert github == {
        "target_key": "github",
        "version": 2,
        "fields": [
            {
                "field": field,
                "configured": configured,
                "reference": build_privacy_field_reference(owner, "credential", "github", field),
            }
            for field, configured in [("pat", True), ("region", False), ("unset", False)]
        ],
    }
    assert all(field["configured"] for field in credential["targets"][0]["fields"])
    assert skill["targets"][0]["fields"][0]["reference"] == build_privacy_field_reference(
        owner, "skill", "demo", "api/key"
    )
    for secret in [
        "historical-secret-marker",
        "old-value-marker",
        "active-secret-marker",
        "skill-secret-marker",
        "other-owner-secret-marker",
        "other-owner-target",
    ]:
        assert secret not in response.text
    assert '"values"' not in response.text
    assert "old_field" not in response.text

    # Activation can select an older version; the tree must follow current, not latest history.
    activated = await client.post(
        "/api/v1/privacy-configs/credential/github/activate", json={"version": 1}
    )
    assert activated.status_code == 200, activated.text
    tree = (await client.get("/api/v1/privacy-configs/tree")).json()["result"]
    assert tree["categories"][0]["targets"][1]["version"] == 1


async def test_privacy_tree_category_filter_and_route_not_shadowed(client: httpx.AsyncClient):
    empty = await client.get("/api/v1/privacy-configs/tree")
    assert empty.status_code == 200, empty.text
    assert empty.json()["result"]["categories"] == []
    for category in ["credential", "skill"]:
        created = await client.post(
            f"/api/v1/privacy-configs/{category}/demo", json={"values": {"token": "secret-marker"}}
        )
        assert created.status_code == 200, created.text
    filtered = await client.get("/api/v1/privacy-configs/tree", params={"category": "skill"})
    assert filtered.status_code == 200, filtered.text
    assert [node["category"] for node in filtered.json()["result"]["categories"]] == ["skill"]
    for category in ["", "..", "bad/category"]:
        invalid = await client.get("/api/v1/privacy-configs/tree", params={"category": category})
        assert invalid.status_code == 400, invalid.text
        assert invalid.json()["error"]["code"] == "INVALID_ARGUMENT"
    missing = await client.get("/api/v1/privacy-configs/tree", params={"category": "missing"})
    assert missing.json()["result"]["categories"] == [{"category": "missing", "targets": []}]


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


async def test_privacy_config_patch_creates_target_and_atomically_merges_fields(
    client: httpx.AsyncClient,
):
    created = await client.patch(
        "/api/v1/privacy-configs/credential/github",
        json={"values": {"pat": "github_pat_mock_value"}, "change_reason": "initial"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["result"]["version"] == 1
    assert created.json()["result"]["values"] == {"pat": "github_pat_mock_value"}

    updated = await client.patch(
        "/api/v1/privacy-configs/credential/github",
        json={"values": {"username": "alice"}, "change_reason": "add username"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["version"] == 2
    assert updated.json()["result"]["values"] == {
        "pat": "github_pat_mock_value",
        "username": "alice",
    }


async def test_privacy_api_rejects_invalid_target_with_structured_format_error(
    client: httpx.AsyncClient,
):
    response = await client.patch(
        "/api/v1/privacy-configs/credential/two@@signs",
        json={"values": {"pat": "github_pat_mock_value"}},
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["message"] == "Invalid Privacy target_key: must contain at most one '@'."
    assert error["details"] == {
        "field": "target_key",
        "reason": "must contain at most one '@'",
        "allowed_pattern": "[A-Za-z0-9_.@-]+",
        "max_characters": 255,
    }


async def test_privacy_api_rejects_oversized_values_without_echoing_secret(
    client: httpx.AsyncClient,
):
    secret = "sensitive-marker-" + "x" * (64 * 1024)
    response = await client.post(
        "/api/v1/privacy-configs/credential/oversized",
        json={"values": {"pat": secret}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert response.json()["error"]["details"]["reason"] == "string value too large"
    assert secret not in response.text


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


async def test_privacy_field_reference_is_safe_by_default_and_reveal_is_explicit(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/privacy-configs/credential/github-prod",
        json={"values": {"api/key": "secret-value", "region": "cn"}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]

    reference_response = await client.get(
        "/api/v1/privacy-configs/credential/github-prod/field",
        params={"field": "api/key"},
    )
    assert reference_response.status_code == 200, reference_response.text
    reference = reference_response.json()["result"]
    assert reference == {
        "owner_user_id": owner,
        "category": "credential",
        "target_key": "github-prod",
        "field": "api/key",
        "version": 1,
        "configured": True,
        "reference": (
            f"{{{{ov_privacy:{owner}:viking://~/privacy/credential/"
            "github-prod#/values/api%2Fkey}}"
        ),
    }
    assert "value" not in reference

    resolved_response = await client.get(
        "/api/v1/privacy-configs/credential/github-prod/field",
        params={"field": "api/key", "reveal": True},
    )
    assert resolved_response.status_code == 200, resolved_response.text
    resolved = resolved_response.json()["result"]
    assert resolved["reference"] == reference["reference"]
    assert resolved["value"] == "secret-value"

    missing_response = await client.get(
        "/api/v1/privacy-configs/credential/github-prod/field",
        params={"field": "missing"},
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "NOT_FOUND"


async def test_privacy_field_list_returns_metadata_without_values(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/privacy-configs/credential/github-fields",
        json={"values": {"region": "", "pat": "github-secret-value"}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]

    response = await client.get(
        "/api/v1/privacy-configs/credential/github-fields/fields"
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result == {
        "owner_user_id": owner,
        "category": "credential",
        "target_key": "github-fields",
        "version": 1,
        "fields": [
            {
                "field": "pat",
                "configured": True,
                "reference": (
                    f"{{{{ov_privacy:{owner}:viking://~/privacy/credential/"
                    "github-fields#/values/pat}}"
                ),
            },
            {
                "field": "region",
                "configured": False,
                "reference": (
                    f"{{{{ov_privacy:{owner}:viking://~/privacy/credential/"
                    "github-fields#/values/region}}"
                ),
            },
        ],
    }
    assert "github-secret-value" not in response.text
    assert all("value" not in field for field in result["fields"])

    missing = await client.get(
        "/api/v1/privacy-configs/credential/missing-fields/fields"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_privacy_config_delete_is_idempotent_and_removes_history(
    client: httpx.AsyncClient,
):
    for value in ("secret-v1", "secret-v2"):
        created = await client.post(
            "/api/v1/privacy-configs/credential/delete-me",
            json={"values": {"api_key": value}},
        )
        assert created.status_code == 200, created.text

    deleted_response = await client.delete(
        "/api/v1/privacy-configs/credential/delete-me"
    )
    assert deleted_response.status_code == 200, deleted_response.text
    assert deleted_response.json()["result"] == {
        "category": "credential",
        "target_key": "delete-me",
        "deleted": True,
    }

    get_response = await client.get("/api/v1/privacy-configs/credential/delete-me")
    assert get_response.status_code == 404

    deleted_again = await client.delete(
        "/api/v1/privacy-configs/credential/delete-me"
    )
    assert deleted_again.status_code == 200, deleted_again.text
    assert deleted_again.json()["result"]["deleted"] is False


async def test_privacy_scan_is_dry_run_by_default_and_fix_rewrites_content(
    client: httpx.AsyncClient,
    service,
):
    secret = "legacy-value-to-repair"
    created = await client.post(
        "/api/v1/privacy-configs/credential/legacy-service",
        json={"values": {"custom_field": secret}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]
    uri = f"viking://user/{owner}/legacy-privacy-content.md"
    ctx = RequestContext(
        user=UserIdentifier(service.user.account_id, owner),
        role=Role.USER,
    )
    raw_path = service.viking_fs._uri_to_path(uri, ctx=ctx)
    await service.viking_fs._async_agfs.write(
        raw_path,
        f"before {secret} after".encode("utf-8"),
    )

    dry_run = await client.post(
        "/api/v1/privacy-configs/scan",
        json={"uri": uri},
    )
    assert dry_run.status_code == 200, dry_run.text
    report = dry_run.json()["result"]
    assert report["dry_run"] is True
    assert report["matched_files"] == 1
    assert report["repaired_files"] == 0
    assert report["matches"][0]["uri"] == uri
    assert report["limits"]["max_nodes"] == 1000
    assert report["limits"]["max_concurrent_scans_per_process"] == 1
    assert report["scanned_bytes"] > 0
    assert secret not in str(report)
    assert secret in await service.viking_fs.read_file(uri, ctx=ctx)

    fixed = await client.post(
        "/api/v1/privacy-configs/scan",
        json={"uri": uri, "fix": True},
    )
    assert fixed.status_code == 200, fixed.text
    fixed_report = fixed.json()["result"]
    assert fixed_report["repaired_files"] == 1
    stored = await service.viking_fs.read_file(uri, ctx=ctx)
    assert secret not in stored
    assert build_privacy_field_reference(
        owner, "credential", "legacy-service", "custom_field"
    ) in stored


async def test_privacy_scan_fix_repairs_skill_and_reindexes_package(
    client: httpx.AsyncClient,
    service,
):
    secret = "legacy-skill-registered-value"
    created = await client.post(
        "/api/v1/privacy-configs/credential/legacy-skill-service",
        json={"values": {"custom": secret}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]
    skill_md = """---
name: privacy-scan-skill
description: Privacy scan fixture
---

# Instructions

Use no credentials yet.
"""
    added = await client.post(
        "/api/v1/skills",
        json={"data": skill_md, "wait": True},
    )
    assert added.status_code == 200, added.text
    skill_uri = f"{added.json()['result']['root_uri']}/SKILL.md"
    ctx = RequestContext(
        user=UserIdentifier(service.user.account_id, owner),
        role=Role.USER,
    )
    raw_path = service.viking_fs._uri_to_path(skill_uri, ctx=ctx)
    await service.viking_fs._async_agfs.write(
        raw_path,
        skill_md.replace("Use no credentials yet.", f"credential={secret}").encode("utf-8"),
    )

    fixed = await client.post(
        "/api/v1/privacy-configs/scan",
        json={"uri": skill_uri, "fix": True},
    )
    assert fixed.status_code == 200, fixed.text
    report = fixed.json()["result"]
    assert report["repaired_files"] == 1
    assert report["reindex"]
    stored = await service.viking_fs.read_file(skill_uri, ctx=ctx)
    assert secret not in stored
    assert build_privacy_field_reference(
        owner,
        "credential",
        "legacy-skill-service",
        "custom",
    ) in stored


async def test_privacy_scan_requires_explicit_uri_and_caps_tree_at_1000_nodes(
    client: httpx.AsyncClient,
):
    for body in (
        {},
        {"scopes": ["resources"]},
        {"uri": "viking://~/resources", "node_limit": 1001},
        {"uri": "viking://~/resources", "wait": True},
    ):
        response = await client.post("/api/v1/privacy-configs/scan", json=body)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_privacy_resolve_accepts_reference_and_field_uri_and_checks_owner(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/privacy-configs/credential/selector-service",
        json={"values": {"api/key": "selector-secret"}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]
    reference = build_privacy_field_reference(
        owner, "credential", "selector-service", "api/key"
    )

    for selector in (
        reference,
        reference.replace("{{ov_privacy:", "{{ ov_privacy:").replace("}}", " }}"),
        "viking://~/privacy/credential/selector-service#/values/api%2Fkey",
        f"viking://user/{owner}/privacy/credential/selector-service#/values/api%2Fkey",
    ):
        resolved = await client.post(
            "/api/v1/privacy-configs/resolve",
            json={"selector": selector},
        )
        assert resolved.status_code == 200, resolved.text
        result = resolved.json()["result"]
        assert result["value"] == "selector-secret"
        assert result["reference"] == reference

    spaced_reference = reference.replace("{{ov_privacy:", "{{ ov_privacy:").replace(
        "}}", " }}"
    )
    normalized_delete = await client.request(
        "DELETE",
        "/api/v1/privacy-configs/selector",
        json={"selector": spaced_reference.replace("api%2Fkey", "missing")},
    )
    assert normalized_delete.status_code == 404

    foreign = reference.replace(f"ov_privacy:{owner}:", "ov_privacy:other-user:")
    denied = await client.post(
        "/api/v1/privacy-configs/resolve",
        json={"selector": foreign},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_privacy_field_delete_publishes_new_active_snapshot_and_keeps_history(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/privacy-configs/credential/delete-field-service",
        json={"values": {"api_key": "secret", "region": "cn"}},
    )
    assert created.status_code == 200, created.text
    owner = created.json()["result"]["created_by"]

    deleted = await client.delete(
        "/api/v1/privacy-configs/credential/delete-field-service/field",
        params={"field": "api_key"},
    )
    assert deleted.status_code == 200, deleted.text
    result = deleted.json()["result"]
    assert result["scope"] == "field"
    assert result["target_deleted"] is False
    assert result["active_version"] == 2

    current = (
        await client.get("/api/v1/privacy-configs/credential/delete-field-service")
    ).json()["result"]["current"]
    assert current["values"] == {"region": "cn"}
    old = (
        await client.get(
            "/api/v1/privacy-configs/credential/delete-field-service/versions/1"
        )
    ).json()["result"]
    assert old["values"]["api_key"] == "secret"

    region_reference = build_privacy_field_reference(
        owner, "credential", "delete-field-service", "region"
    )
    deleted_last = await client.request(
        "DELETE",
        "/api/v1/privacy-configs/selector",
        json={"selector": region_reference},
    )
    assert deleted_last.status_code == 200, deleted_last.text
    last_result = deleted_last.json()["result"]
    assert last_result["target_deleted"] is True
    assert last_result["active_version"] is None
    missing_target = await client.get(
        "/api/v1/privacy-configs/credential/delete-field-service"
    )
    assert missing_target.status_code == 404
    missing_history = await client.get(
        "/api/v1/privacy-configs/credential/delete-field-service/versions/1"
    )
    assert missing_history.status_code == 404

    missing = await client.request(
        "DELETE",
        "/api/v1/privacy-configs/selector",
        json={"selector": region_reference},
    )
    assert missing.status_code == 404


async def test_privacy_delete_selector_accepts_target_uri(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/privacy-configs/credential/delete-target-uri",
        json={"values": {"key": "value"}},
    )
    assert created.status_code == 200, created.text
    deleted = await client.request(
        "DELETE",
        "/api/v1/privacy-configs/selector",
        json={"selector": "viking://~/privacy/credential/delete-target-uri"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["result"]["scope"] == "target"
