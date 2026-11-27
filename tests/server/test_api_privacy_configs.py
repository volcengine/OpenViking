# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import hashlib
import re
from dataclasses import replace

import httpx
import yaml

from openviking.privacy.reference import build_privacy_field_reference
from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import patch_user_config
from openviking_cli.session.user_id import UserIdentifier


async def test_privacy_tree_lists_current_fields_without_values(client: httpx.AsyncClient, service):
    empty = await client.get("/api/v1/privacy-configs/tree")
    assert empty.status_code == 200, empty.text
    assert empty.json()["result"]["categories"] == []

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
        "/api/v1/privacy-configs/credential/demo-skill",
        json={
            "values": {"api_key": "sample"},
            "change_reason": "seed",
        },
    )
    assert upsert.status_code == 200
    body = upsert.json()
    assert body["status"] == "ok"
    assert body["result"]["version"] == 1
    assert [(warning["code"], warning["field"]) for warning in body["result"]["warnings"]] == [
        ("weak_credential", "api_key")
    ]
    assert "sample" not in str(body["result"]["warnings"])

    get_current = await client.get("/api/v1/privacy-configs/credential/demo-skill")
    assert get_current.status_code == 200
    current_body = get_current.json()
    assert current_body["result"]["current"]["values"]["api_key"] == "sample"
    assert current_body["result"]["meta"]["active_version"] == 1

    second_values = {"api_key": "secret-2", "new_key": "x"}
    second = await client.post(
        "/api/v1/privacy-configs/credential/demo-skill",
        json={
            "values": second_values,
            "change_reason": "rotate",
        },
    )
    assert second.status_code == 200
    assert second.json()["result"]["version"] == 2
    assert second.json()["result"]["values"] == second_values

    versions = await client.get("/api/v1/privacy-configs/credential/demo-skill/versions")
    assert versions.status_code == 200
    assert versions.json()["result"] == [1, 2]
    missing = await client.get("/api/v1/privacy-configs/credential/demo-skill/versions/10")
    assert missing.status_code == 404
    assert missing.json()["status"] == "error"
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    activate = await client.post(
        "/api/v1/privacy-configs/credential/demo-skill/activate",
        json={"version": 1},
    )
    assert activate.status_code == 200
    assert activate.json()["result"]["version"] == 1

    final_current = await client.get("/api/v1/privacy-configs/credential/demo-skill")
    assert final_current.json()["result"]["current"]["values"] == {"api_key": "sample"}


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
    expected = {"pat": "github_pat_mock_value", "username": "alice"}
    assert updated.json()["result"]["values"] == expected
    current = await client.get("/api/v1/privacy-configs/credential/github")
    assert current.status_code == 200, current.text
    assert current.json()["result"]["current"]["values"] == expected


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

    response = await client.get("/api/v1/privacy-configs/credential/github-fields/fields")
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

    missing = await client.get("/api/v1/privacy-configs/credential/missing-fields/fields")
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

    deleted_response = await client.delete("/api/v1/privacy-configs/credential/delete-me")
    assert deleted_response.status_code == 200, deleted_response.text
    assert deleted_response.json()["result"] == {
        "category": "credential",
        "target_key": "delete-me",
        "deleted": True,
    }

    get_response = await client.get("/api/v1/privacy-configs/credential/delete-me")
    assert get_response.status_code == 404

    deleted_again = await client.delete("/api/v1/privacy-configs/credential/delete-me")
    assert deleted_again.status_code == 200, deleted_again.text
    assert deleted_again.json()["result"]["deleted"] is False

    recreated = await client.post(
        "/api/v1/privacy-configs/credential/delete-me",
        json={"values": {"api_key": "replacement"}},
    )
    assert recreated.status_code == 200, recreated.text
    assert recreated.json()["result"]["version"] == 1
    old_version = await client.get("/api/v1/privacy-configs/credential/delete-me/versions/2")
    assert old_version.status_code == 404
    assert old_version.json()["error"]["code"] == "NOT_FOUND"


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
    uri = f"viking://user/{owner}/legacy-privacy-content.yaml"
    ctx = RequestContext(
        user=UserIdentifier(service.user.account_id, owner),
        role=Role.USER,
    )
    raw_path = service.viking_fs._uri_to_path(uri, ctx=ctx)
    await service.viking_fs._async_agfs.write(
        raw_path,
        f"display: &label {secret} # keep comment\nlookup: {{*label: 23}}\nitems: [true, null, 42]\n".encode(
            "utf-8"
        ),
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
    reference = build_privacy_field_reference(owner, "credential", "legacy-service", "custom_field")
    assert yaml.safe_load(stored) == {
        "display": reference,
        "lookup": {secret: 23},
        "items": [True, None, 42],
    }
    assert "# keep comment" in stored


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
    assert (
        build_privacy_field_reference(
            owner,
            "credential",
            "legacy-skill-service",
            "custom",
        )
        in stored
    )


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
    reference = build_privacy_field_reference(owner, "credential", "selector-service", "api/key")

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

    spaced_reference = reference.replace("{{ov_privacy:", "{{ ov_privacy:").replace("}}", " }}")
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

    current = (await client.get("/api/v1/privacy-configs/credential/delete-field-service")).json()[
        "result"
    ]["current"]
    assert current["values"] == {"region": "cn"}
    old = (
        await client.get("/api/v1/privacy-configs/credential/delete-field-service/versions/1")
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
    missing_target = await client.get("/api/v1/privacy-configs/credential/delete-field-service")
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


def _github_token(seed: str) -> str:
    # Assembled at runtime; a synthetic GitHub-shaped token for detector tests.
    return "ghp_" + hashlib.sha256(seed.encode()).hexdigest()[:36]


async def _seal(client: httpx.AsyncClient, *texts) -> httpx.Response:
    return await client.post(
        "/api/v1/privacy-configs/seal",
        json={"texts": [item if isinstance(item, dict) else {"text": item} for item in texts]},
    )


async def _owner_ctx(client: httpx.AsyncClient, service) -> RequestContext:
    tree = await client.get("/api/v1/privacy-configs/tree")
    owner = tree.json()["result"]["owner_user_id"]
    return RequestContext(user=UserIdentifier(service.user.account_id, owner), role=Role.USER)


async def _credential_targets(client: httpx.AsyncClient) -> list[str]:
    response = await client.get("/api/v1/privacy-configs/credential")
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def _auto_targets(client: httpx.AsyncClient) -> list[str]:
    response = await client.get("/api/v1/privacy-configs/auto")
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def test_privacy_seal_replaces_registered_values_when_mode_is_off(
    client: httpx.AsyncClient, service
):
    ctx = await _owner_ctx(client, service)
    assert (await service.privacy_configs.get_settings(ctx)).mode == "off"
    created = await client.post(
        "/api/v1/privacy-configs/credential/svc", json={"values": {"token": "seal-registered-1"}}
    )
    assert created.status_code == 200, created.text
    reference = build_privacy_field_reference(ctx.user.user_id, "credential", "svc", "token")

    response = await _seal(client, "key: seal-registered-1.", "", "nothing here")
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["texts"] == [
        {
            "text": f"key: {reference}.",
            "replaced": [
                {
                    "start": 5,
                    "end": 22,
                    "reference": reference,
                    "created": False,
                    "temporary": False,
                }
            ],
        },
        None,
        None,
    ]
    revision = result["revision"]
    assert revision and revision.isascii() and len(revision) <= 128
    again = (await _seal(client, "nothing here")).json()["result"]
    assert again == {"revision": revision, "texts": [None]}


async def test_privacy_seal_replaces_a_new_token_typed_next_to_cjk_text(client: httpx.AsyncClient):
    token = _github_token("cjk-adjacent")
    response = await _seal(
        client,
        {"text": f"密钥是{token}请保存", "enroll": True},
        f"历史消息里的{token}也要替换",
    )
    assert response.status_code == 200, response.text
    sealed, history = response.json()["result"]["texts"]
    reference = sealed["replaced"][0]["reference"]
    assert sealed["replaced"][0]["created"] is True
    assert sealed["text"] == f"密钥是{reference}请保存"
    assert history["text"] == f"历史消息里的{reference}也要替换"


async def test_privacy_seal_enrolls_new_tokens_once(client: httpx.AsyncClient, service):
    token = _github_token("enroll-once")
    first = await _seal(
        client,
        {"text": f"my new token {token}", "enroll": True},
        f"tool output mentions {token} too",
    )
    assert first.status_code == 200, first.text
    first_result = first.json()["result"]
    sealed, output = first_result["texts"]
    reference = sealed["replaced"][0]["reference"]
    assert sealed["text"] == f"my new token {reference}"
    assert sealed["replaced"] == [
        {
            "start": 13,
            "end": 13 + len(token),
            "reference": reference,
            "created": True,
            "temporary": True,
        }
    ]
    assert output["replaced"][0]["created"] is True
    assert output["text"] == f"tool output mentions {reference} too"

    assert await _credential_targets(client) == []
    targets = await _auto_targets(client)
    assert len(targets) == 1
    target = targets[0]
    assert re.fullmatch(r"github-[a-z0-9]+", target)
    assert reference.endswith(f"/privacy/auto/{target}#/values/token}}}}")
    current = (await client.get(f"/api/v1/privacy-configs/auto/{target}")).json()["result"]
    assert current["meta"]["labels"] == {"ov.rule": "github"}
    resolved = await client.post("/api/v1/privacy-configs/resolve", json={"selector": reference})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["result"]["value"] == token

    second = (await _seal(client, {"text": f"again {token}", "enroll": True})).json()["result"]
    assert second["texts"][0]["replaced"][0] == {
        "start": 6,
        "end": 6 + len(token),
        "reference": reference,
        "created": False,
        # Still an auto entry, whichever call enrolled it.
        "temporary": True,
    }
    # The first call's revision already included its own enrollment.
    assert second["revision"] == first_result["revision"]
    third = (await _seal(client, {"text": f"again {token}", "enroll": True})).json()["result"]
    assert third["revision"] == second["revision"]
    assert await _auto_targets(client) == [target]

    other = _github_token("not-enrolled")
    plain = (await _seal(client, f"unflagged {other}")).json()["result"]
    assert plain["texts"] == [None]
    assert plain["revision"] == second["revision"]
    assert await _auto_targets(client) == [target]


async def test_privacy_seal_follows_never_mask_and_custom_rules(client: httpx.AsyncClient, service):
    ctx = await _owner_ctx(client, service)
    public = _github_token("public")
    await client.post(
        "/api/v1/privacy-configs/credential/registered",
        json={"values": {"token": "registered-public-1"}},
    )
    before = (await _seal(client, "x")).json()["result"]["revision"]

    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate(
            {"privacy": {"never_mask": [public, "registered-public-1"]}}
        ),
    )
    masked = (
        await _seal(client, {"text": f"{public} registered-public-1", "enroll": True})
    ).json()["result"]
    assert masked["texts"] == [None]
    assert masked["revision"] != before
    assert await _credential_targets(client) == ["registered"]
    assert await _auto_targets(client) == []

    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate(
            {"privacy": {"custom_rules": [{"id": "corp", "prefix": "corp_", "min_length": 12}]}}
        ),
    )
    custom = (await _seal(client, {"text": "use corp_abcdef123456 now", "enroll": True})).json()[
        "result"
    ]
    assert custom["revision"] != masked["revision"]
    replaced = custom["texts"][0]["replaced"]
    assert [(item["start"], item["end"], item["created"]) for item in replaced] == [(4, 21, True)]
    targets = await _auto_targets(client)
    assert len(targets) == 1 and targets[0].startswith("corp-")
    meta = (await client.get(f"/api/v1/privacy-configs/auto/{targets[0]}")).json()
    assert meta["result"]["meta"]["labels"] == {"ov.rule": "corp"}


async def test_privacy_seal_rejects_invalid_requests_and_limits(
    client: httpx.AsyncClient, service, monkeypatch
):
    for body in (
        {"texts": []},
        {"texts": [{"text": "x"}] * 1025},
        {"texts": [{"text": 1}]},
        {"texts": [{"text": "x", "unknown": True}]},
        {"values": {"token": "not-an-upsert"}},
    ):
        response = await client.post("/api/v1/privacy-configs/seal", json=body)
        assert 400 <= response.status_code < 500, response.text
        assert response.json()["status"] != "ok"

    oversized = await _seal(client, "a" * (8 * 1024 * 1024), "b" * (8 * 1024 * 1024 + 1))
    assert oversized.status_code == 429, oversized.status_code
    assert oversized.json()["error"]["code"] == "RESOURCE_EXHAUSTED"

    await client.post(
        "/api/v1/privacy-configs/credential/limit", json={"values": {"token": "limit-secret-1"}}
    )
    monkeypatch.setattr(service.privacy_sanitizer, "_max_replacements", 2)
    limited = await _seal(client, "limit-secret-1 limit-secret-1", "limit-secret-1")
    assert limited.status_code == 429, limited.text
    assert "limit-secret-1" not in limited.text

    monkeypatch.setattr(service, "_privacy_config_service", None)
    unavailable = await _seal(client, "limit-secret-1")
    assert unavailable.status_code >= 400, unavailable.text
    assert unavailable.json()["status"] != "ok"


async def test_privacy_seal_route_is_not_shadowed_and_revision_is_not_a_config(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/privacy-configs/credential/seal", json={"values": {"token": "named-seal-1"}}
    )
    assert created.status_code == 200, created.text
    assert (await client.get("/api/v1/privacy-configs/credential/seal")).status_code == 200
    sealed = await _seal(client, "named-seal-1")
    assert sealed.status_code == 200, sealed.text
    assert sealed.json()["result"]["texts"][0]["replaced"][0]["reference"].endswith(
        "/credential/seal#/values/token}}"
    )

    assert (await client.get("/api/v1/privacy-configs")).json()["result"] == ["credential"]
    tree = (await client.get("/api/v1/privacy-configs/tree")).json()["result"]
    assert [node["category"] for node in tree["categories"]] == ["credential"]
    assert [node["target_key"] for node in tree["categories"][0]["targets"]] == ["seal"]


async def test_privacy_upsert_accepts_reference_value_and_removes_enrolled_source(
    client: httpx.AsyncClient,
):
    token = _github_token("rename")
    sealed = (await _seal(client, {"text": token, "enroll": True})).json()["result"]
    reference = sealed["texts"][0]["replaced"][0]["reference"]
    [temporary] = await _auto_targets(client)

    renamed = await client.patch(
        "/api/v1/privacy-configs/credential/github", json={"values": {"pat": reference}}
    )
    assert renamed.status_code == 200, renamed.text
    # The response never echoes the value copied from the reference.
    owner = renamed.json()["result"]["created_by"]
    named = build_privacy_field_reference(owner, "credential", "github", "pat")
    assert renamed.json()["result"]["values"] == {"pat": named}
    assert token not in renamed.text
    assert "warnings" not in renamed.json()["result"]
    assert await _credential_targets(client) == ["github"]
    assert await _auto_targets(client) == []
    missing = await client.get(f"/api/v1/privacy-configs/auto/{temporary}")
    assert missing.status_code == 404

    stale = await client.post(
        "/api/v1/privacy-configs/credential/github", json={"values": {"pat": reference}}
    )
    assert stale.status_code == 400, stale.text
    assert stale.json()["error"]["code"] == "INVALID_ARGUMENT"
    current = (await client.get("/api/v1/privacy-configs/credential/github")).json()["result"]
    assert current["current"]["values"] == {"pat": token}

    # Once saved under a name the value is no longer temporary.
    after = (await _seal(client, {"text": token, "enroll": True})).json()["result"]
    [span] = after["texts"][0]["replaced"]
    assert (span["reference"], span["created"], span["temporary"]) == (named, False, False)


async def test_privacy_write_responses_hide_values_when_a_reference_was_given(
    client: httpx.AsyncClient,
):
    first = _github_token("echo-first")
    second = _github_token("echo-second")
    sealed = (await _seal(client, {"text": f"{first} {second}", "enroll": True})).json()["result"]
    refs = [item["reference"] for item in sealed["texts"][0]["replaced"]]

    upserted = await client.post(
        "/api/v1/privacy-configs/credential/github",
        json={"values": {"pat": refs[0], "username": "octo-user"}},
    )
    assert upserted.status_code == 200, upserted.text
    owner = upserted.json()["result"]["created_by"]

    def ref(field: str) -> str:
        return build_privacy_field_reference(owner, "credential", "github", field)

    # Every stored value is shown as its own reference, the plain one too.
    assert upserted.json()["result"]["values"] == {"pat": ref("pat"), "username": ref("username")}
    assert first not in upserted.text and "octo-user" not in upserted.text

    patched = await client.patch(
        "/api/v1/privacy-configs/credential/github", json={"values": {"backup": refs[1]}}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["result"]["values"] == {
        "backup": ref("backup"),
        "pat": ref("pat"),
        "username": ref("username"),
    }
    assert first not in patched.text and second not in patched.text

    # A request without references keeps today's response.
    plain = await client.patch(
        "/api/v1/privacy-configs/credential/github", json={"values": {"username": "octo-2"}}
    )
    assert plain.status_code == 200, plain.text
    assert plain.json()["result"]["values"] == {
        "backup": second,
        "pat": first,
        "username": "octo-2",
    }


async def test_privacy_seal_matches_earlier_versions_of_credentials(
    client: httpx.AsyncClient, service
):
    ctx = await _owner_ctx(client, service)
    old = _github_token("rotation-old")
    new = _github_token("rotation-new")
    for token in (old, new):
        saved = await client.post(
            "/api/v1/privacy-configs/credential/github", json={"values": {"token": token}}
        )
        assert saved.status_code == 200, saved.text
    reference = build_privacy_field_reference(ctx.user.user_id, "credential", "github", "token")

    result = (await _seal(client, {"text": f"old {old} new {new}", "enroll": True})).json()[
        "result"
    ]
    assert result["texts"][0]["text"] == f"old {reference} new {reference}"
    assert [(item["created"], item["temporary"]) for item in result["texts"][0]["replaced"]] == [
        (False, False),
        (False, False),
    ]
    # The earlier value is sealed, not enrolled again.
    assert await _credential_targets(client) == ["github"]
    resolved = await client.post("/api/v1/privacy-configs/resolve", json={"selector": reference})
    assert resolved.json()["result"]["value"] == new

    # A current value of another field decides the reference of a shared value.
    await client.post("/api/v1/privacy-configs/credential/other", json={"values": {"key": old}})
    other = build_privacy_field_reference(ctx.user.user_id, "credential", "other", "key")
    shared = (await _seal(client, old)).json()["result"]
    assert shared["texts"][0]["text"] == other
    await client.delete("/api/v1/privacy-configs/credential/other")

    # The storage-path sanitizer still matches current values only.
    settings = replace(await service.privacy_configs.get_settings(ctx), mode="registered")
    stored = await service.privacy_sanitizer.sanitize(f"{old} {new}", ctx=ctx, settings=settings)
    assert stored.content == f"{old} {reference}"

    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"privacy": {"never_mask": [old]}}),
    )
    masked = (await _seal(client, f"{old} {new}")).json()["result"]
    assert masked["texts"][0]["text"] == f"{old} {reference}"
    await patch_user_config(
        service.viking_fs, ctx, UserSettingsPatch.model_validate({"privacy": {"never_mask": []}})
    )

    deleted = await client.delete("/api/v1/privacy-configs/credential/github")
    assert deleted.json()["result"]["deleted"] is True
    gone = (await _seal(client, f"{old} {new}")).json()["result"]
    assert gone["texts"] == [None]


async def test_privacy_auto_reference_resolves_and_is_managed_like_any_target(
    client: httpx.AsyncClient, service
):
    ctx = await _owner_ctx(client, service)
    token = _github_token("auto-category")
    sealed = (await _seal(client, {"text": token, "enroll": True})).json()["result"]
    reference = sealed["texts"][0]["replaced"][0]["reference"]
    [target] = await _auto_targets(client)
    assert reference == build_privacy_field_reference(ctx.user.user_id, "auto", target, "token")

    # The reference resolves.
    resolved = await client.post("/api/v1/privacy-configs/resolve", json={"selector": reference})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["result"]["value"] == token
    assert resolved.json()["result"]["category"] == "auto"

    # A Credential holding the same value wins, and its value is no longer temporary.
    await client.post("/api/v1/privacy-configs/credential/named", json={"values": {"pat": token}})
    named = build_privacy_field_reference(ctx.user.user_id, "credential", "named", "pat")
    [span] = (await _seal(client, token)).json()["result"]["texts"][0]["replaced"]
    assert (span["reference"], span["temporary"]) == (named, False)
    # Saving by plain value keeps the auto entry; only a reference move removes it.
    assert await _auto_targets(client) == [target]

    # The selector delete removes an auto entry and changes the revision.
    before = (await _seal(client, "x")).json()["result"]["revision"]
    deleted = await client.request(
        "DELETE",
        "/api/v1/privacy-configs/selector",
        json={"selector": f"viking://~/privacy/auto/{target}"},
    )
    assert deleted.status_code == 200, deleted.text
    assert await _auto_targets(client) == []
    assert (await _seal(client, "x")).json()["result"]["revision"] != before
    missing = await client.post("/api/v1/privacy-configs/resolve", json={"selector": reference})
    assert missing.status_code == 404, missing.text


async def test_privacy_seal_leaves_a_match_too_long_to_store(client: httpx.AsyncClient):
    registered = _github_token("too-long-registered")
    created = await client.post(
        "/api/v1/privacy-configs/credential/github", json={"values": {"token": registered}}
    )
    assert created.status_code == 200, created.text
    # Detector patterns have no upper bound; a stored value does (64 KiB).
    huge = "ghp_" + "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(1100))
    response = await _seal(client, {"text": f"{registered} then {huge}", "enroll": True})
    assert response.status_code == 200, response.text
    [text] = response.json()["result"]["texts"]
    # The registered value is still replaced; the over-long match stays as it is.
    assert [span["end"] - span["start"] for span in text["replaced"]] == [len(registered)]
    assert text["text"].endswith(f" then {huge}")
    assert await _auto_targets(client) == []


async def test_privacy_seal_caps_new_enrollments_per_call(client: httpx.AsyncClient):
    from openviking.privacy.seal import SEAL_MAX_ENROLLMENTS

    assert SEAL_MAX_ENROLLMENTS == 1024
    text = " ".join(_github_token(f"cap-{index}") for index in range(SEAL_MAX_ENROLLMENTS + 1))
    response = await _seal(client, {"text": text, "enroll": True})
    assert response.status_code == 429, response.status_code
    assert response.json()["error"]["code"] == "RESOURCE_EXHAUSTED"
    assert await _auto_targets(client) == []


async def test_privacy_seal_matches_every_enrolled_value_past_one_listing_page(
    client: httpx.AsyncClient,
):
    # A directory listing returns 1000 entries unless asked for all of them.
    tokens = [_github_token(f"page-{index}") for index in range(1001)]
    response = await _seal(client, {"text": " ".join(tokens), "enroll": True})
    assert response.status_code == 200, response.text
    [text] = response.json()["result"]["texts"]
    assert len(text["replaced"]) == len(tokens)
    assert not any(token in text["text"] for token in tokens)
    assert len(await _auto_targets(client)) == len(tokens)

    again = await _seal(client, {"text": " ".join(tokens), "enroll": True})
    assert again.status_code == 200, again.text
    assert len(again.json()["result"]["texts"][0]["replaced"]) == len(tokens)
    assert len(await _auto_targets(client)) == len(tokens)
