# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import io
import json
import sys
import types
import zipfile
from unittest.mock import AsyncMock

import pytest
from starlette.responses import PlainTextResponse

from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import patch_user_config
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _stub_mcp_endpoint(monkeypatch):
    """Keep these router tests independent from the optional MCP package."""

    module = types.ModuleType("openviking.server.mcp_endpoint")

    def create_mcp_app():
        async def _endpoint(_request):
            return PlainTextResponse("mcp stub")

        return _endpoint

    module.create_mcp_app = create_mcp_app
    monkeypatch.setitem(sys.modules, "openviking.server.mcp_endpoint", module)


def _skill_md(name: str, description: str, body: str = "Use this skill for testing.") -> str:
    return f"""---
name: {name}
description: {description}
tags:
  - test
---

# {name}

## Instructions
{body}
"""


async def _add_skill(client, name: str = "api-skill", description: str = "API skill"):
    response = await client.post(
        "/api/v1/skills",
        json={"data": _skill_md(name, description), "wait": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def test_skills_api_list_empty_collection(client):
    response = await client.get("/api/v1/skills")
    assert response.status_code == 200, response.text
    listed = response.json()["result"]
    assert listed["skills"] == []
    assert listed["total"] == 0


async def test_skills_api_list_honors_node_limit(client):
    await _add_skill(client, "limit-skill-a", "First skill")
    await _add_skill(client, "limit-skill-b", "Second skill")

    limited = await client.get("/api/v1/skills", params={"node_limit": 1})
    assert limited.status_code == 200, limited.text
    assert limited.json()["result"]["total"] == 1

    default = await client.get("/api/v1/skills", params={"node_limit": 0})
    assert default.status_code == 200, default.text
    assert default.json()["result"]["total"] == 2


async def test_skills_api_list_show_find_and_delete(client):
    added = await _add_skill(client, "api-skill", "API skill for list and show")
    assert added["uri"].endswith("/skills/api-skill")

    privacy_response = await client.post(
        "/api/v1/privacy-configs/skill/api-skill",
        json={"values": {"api_key": "secret-1"}, "change_reason": "seed"},
    )
    assert privacy_response.status_code == 200, privacy_response.text

    list_response = await client.get("/api/v1/skills")
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()["result"]
    assert listed["total"] >= 1
    assert any(skill["name"] == "api-skill" for skill in listed["skills"])

    show_response = await client.get(
        "/api/v1/skills/api-skill",
        params={
            "level": 2,
            "include_files": True,
            "include_integrity": True,
            "include_source": True,
        },
    )
    assert show_response.status_code == 200, show_response.text
    shown = show_response.json()["result"]
    assert shown["name"] == "api-skill"
    assert shown["description"] == "API skill for list and show"
    assert shown["skill_md_uri"].endswith("/skills/api-skill/SKILL.md")
    assert "# api-skill" in shown["content"]
    assert any(file["name"] == "SKILL.md" for file in shown["files"])
    assert len(shown["revision"]) == 64
    assert len(shown["content_sha256"]) == 64
    assert all(
        file.get("is_dir") or (file.get("size") is not None and len(file.get("sha256", "")) == 64)
        for file in shown["files"]
    )
    assert shown["source"]["tracked"] is True
    assert shown["source"]["type"] == "api"
    assert shown["source"]["source"] == "inline_content"
    assert shown["source"]["operation"] == "add"
    assert shown["source"]["skill_name"] == "api-skill"

    level_zero_response = await client.get(
        "/api/v1/skills/api-skill",
        params={"level": 0},
    )
    assert level_zero_response.status_code == 200, level_zero_response.text
    level_zero = level_zero_response.json()["result"]
    assert "abstract" in level_zero
    assert "overview" not in level_zero
    assert "content" not in level_zero

    level_one_response = await client.get(
        "/api/v1/skills/api-skill",
        params={"level": 1},
    )
    assert level_one_response.status_code == 200, level_one_response.text
    level_one = level_one_response.json()["result"]
    assert "abstract" not in level_one
    assert "overview" in level_one
    assert "content" not in level_one

    level_two_response = await client.get(
        "/api/v1/skills/api-skill",
        params={"level": 2},
    )
    assert level_two_response.status_code == 200, level_two_response.text
    level_two = level_two_response.json()["result"]
    assert "abstract" not in level_two
    assert "overview" not in level_two
    assert "# api-skill" in level_two["content"]

    default_response = await client.get("/api/v1/skills/api-skill")
    assert default_response.status_code == 200, default_response.text
    default_show = default_response.json()["result"]
    assert "abstract" in default_show
    assert "overview" in default_show
    assert "content" in default_show

    no_content_response = await client.get(
        "/api/v1/skills/api-skill",
        params={"include_content": False},
    )
    assert no_content_response.status_code == 200, no_content_response.text
    no_content = no_content_response.json()["result"]
    assert "abstract" in no_content
    assert "overview" in no_content
    assert "content" not in no_content

    level_one_with_content_response = await client.get(
        "/api/v1/skills/api-skill",
        params={"level": 1, "include_content": True},
    )
    assert level_one_with_content_response.status_code == 200, level_one_with_content_response.text
    level_one_with_content = level_one_with_content_response.json()["result"]
    assert "abstract" not in level_one_with_content
    assert "overview" in level_one_with_content
    assert "content" in level_one_with_content

    find_response = await client.post(
        "/api/v1/skills/find",
        json={"query": "list and show", "limit": 5},
    )
    assert find_response.status_code == 200, find_response.text
    found = find_response.json()["result"]
    assert "skills" in found
    assert "total" in found

    delete_response = await client.delete("/api/v1/skills/api-skill")
    assert delete_response.status_code == 200, delete_response.text
    deleted = delete_response.json()["result"]
    assert deleted["name"] == "api-skill"
    assert deleted["privacy_deleted"] is True

    missing_response = await client.get("/api/v1/skills/api-skill")
    assert missing_response.status_code == 404

    missing_privacy_response = await client.get("/api/v1/privacy-configs/skill/api-skill")
    assert missing_privacy_response.status_code == 404


async def test_skill_ownership_and_change_history_survive_update_and_delete(client):
    created = await client.post(
        "/api/v1/skills",
        headers={"X-Request-ID": "skill-create-request"},
        json={
            "data": _skill_md("history-skill", "Original description"),
            "source_metadata": {
                "type": "api",
                "owner_user_id": "payload-must-not-be-trusted",
                "created_by": "payload-must-not-be-trusted",
            },
            "wait": True,
        },
    )
    assert created.status_code == 200, created.text
    created_result = created.json()["result"]
    assert created_result["owner_user_id"] == "default"
    assert created_result["created_by"] == "default"
    original_created_at = created_result["created_at"]

    listed = await client.get("/api/v1/skills")
    skill = next(
        item for item in listed.json()["result"]["skills"] if item["name"] == "history-skill"
    )
    assert skill["owner_user_id"] == "default"
    assert skill["updated_by"] == "default"
    assert skill["created_at"] == original_created_at

    updated = await client.put(
        "/api/v1/skills/history-skill",
        headers={"X-Request-ID": "skill-update-request"},
        json={
            "data": _skill_md("history-skill", "Updated description", "Changed instructions"),
            "wait": True,
        },
    )
    assert updated.status_code == 200, updated.text
    shown = await client.get("/api/v1/skills/history-skill")
    shown_result = shown.json()["result"]
    assert shown_result["owner_user_id"] == "default"
    assert shown_result["created_at"] == original_created_at
    assert shown_result["current_revision"] != created_result["current_revision"]
    assert [event["operation"] for event in shown_result["history"]] == ["create", "update"]
    assert shown_result["history"][0]["before_hash"] is None
    assert shown_result["history"][1]["before_hash"] == created_result["current_revision"]
    assert shown_result["history"][1]["after_hash"] == shown_result["current_revision"]
    assert shown_result["history"][1]["request_id"] == "skill-update-request"

    deleted = await client.delete(
        "/api/v1/skills/history-skill",
        headers={"X-Request-ID": "skill-delete-request"},
    )
    assert deleted.status_code == 200, deleted.text
    audit = await client.get("/api/v1/skills/history-skill/audit")
    assert audit.status_code == 200, audit.text
    record = audit.json()["result"]
    assert record["deleted"] is True
    assert record["owner_user_id"] == "default"
    assert record["created_at"] == original_created_at
    assert [event["operation"] for event in record["events"]] == ["create", "update", "delete"]
    assert record["events"][-1]["before_hash"] == shown_result["current_revision"]
    assert record["events"][-1]["after_hash"] is None
    assert record["events"][-1]["request_id"] == "skill-delete-request"


async def test_generic_mutation_apis_cannot_bypass_skill_history(client):
    write = await client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://agent/skills/example/SKILL.md",
            "content": "untracked change",
        },
    )
    assert write.status_code == 400
    assert "use the Skill API" in write.json()["error"]["message"]

    removed = await client.delete(
        "/api/v1/fs",
        params={"uri": "viking://agent/skills/example", "recursive": True},
    )
    assert removed.status_code == 400
    assert "use the Skill API" in removed.json()["error"]["message"]

    restored = await client.post(
        "/api/v1/snapshot/restore",
        json={
            "path": "viking://agent/skills/example/SKILL.md",
            "restore_commit_id": "0" * 40,
        },
    )
    assert restored.status_code == 400
    assert "use the Skill API" in restored.json()["error"]["message"]


async def test_skills_api_update_requires_matching_name(client):
    await _add_skill(client, "update-skill", "Original description")

    mismatch_response = await client.put(
        "/api/v1/skills/update-skill",
        json={"data": _skill_md("other-skill", "Wrong name"), "wait": True},
    )
    assert mismatch_response.status_code == 400
    assert mismatch_response.json()["error"]["code"] == "INVALID_ARGUMENT"
    unchanged = await client.get("/api/v1/skills/update-skill")
    assert unchanged.json()["result"]["description"] == "Original description"

    update_response = await client.put(
        "/api/v1/skills/update-skill",
        json={
            "data": _skill_md(
                "update-skill",
                "Updated description",
                "Updated instructions from the replacement payload.",
            ),
            "wait": True,
        },
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["result"]["action"] == "update"

    show_response = await client.get(
        "/api/v1/skills/update-skill",
        params={"include_content": True, "include_source": True},
    )
    shown = show_response.json()["result"]
    assert shown["description"] == "Updated description"
    assert "Updated instructions" in shown["content"]
    assert shown["source"]["tracked"] is True
    assert shown["source"]["type"] == "api"
    assert shown["source"]["source"] == "inline_content"
    assert shown["source"]["operation"] == "update"
    assert shown["source"]["skill_name"] == "update-skill"


async def test_skills_api_update_accepts_temp_uploaded_single_file_with_arbitrary_name(
    client,
    tmp_path,
):
    await _add_skill(client, "update-temp-file-skill", "Original description")

    skill_file = tmp_path / "custom-update-name.md"
    skill_file.write_text(
        _skill_md("update-temp-file-skill", "Updated from uploaded file"),
        encoding="utf-8",
    )

    with skill_file.open("rb") as handle:
        upload_response = await client.post(
            "/api/v1/resources/temp_upload",
            files={"file": ("custom-update-name.md", handle, "text/markdown")},
        )
    assert upload_response.status_code == 200, upload_response.text
    temp_file_id = upload_response.json()["result"]["temp_file_id"]

    update_response = await client.put(
        "/api/v1/skills/update-temp-file-skill",
        json={"temp_file_id": temp_file_id, "wait": True},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["result"]["action"] == "update"

    show_response = await client.get(
        "/api/v1/skills/update-temp-file-skill",
        params={"include_content": True, "include_source": True},
    )
    shown = show_response.json()["result"]
    assert shown["description"] == "Updated from uploaded file"
    assert shown["source"]["original_filename"] == "custom-update-name.md"


async def test_skills_api_update_keeps_replacement_when_metadata_write_fails(client, monkeypatch):
    await _add_skill(client, "failed-update-skill", "Original description")
    endpoint = "/api/v1/privacy-configs/skill/failed-update-skill"
    seeded = await client.post(endpoint, json={"values": {"api_key": "secret-old"}})
    assert seeded.status_code == 200, seeded.text

    async def _fail_persist(*_args, **_kwargs):
        raise RuntimeError("source metadata write failed")

    monkeypatch.setattr(
        "openviking.server.skill_source_metadata.write_skill_source_metadata",
        _fail_persist,
    )

    response = await client.put(
        "/api/v1/skills/failed-update-skill",
        json={
            "data": _skill_md(
                "failed-update-skill",
                "Updated description",
                "This replacement remains after failure.",
            ),
            "wait": True,
        },
    )
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "INTERNAL"

    show_response = await client.get(
        "/api/v1/skills/failed-update-skill",
        params={"include_content": True},
    )
    assert show_response.status_code == 200, show_response.text
    shown = show_response.json()["result"]
    assert shown["description"] == "Updated description"
    assert "This replacement remains after failure." in shown["content"]
    current = (await client.get(endpoint)).json()["result"]["current"]
    assert current == seeded.json()["result"]


async def test_skills_api_update_retains_privacy_until_explicit_delete(client, service):
    from openviking.message import TextPart
    from openviking.privacy.reference import build_privacy_field_reference

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await patch_user_config(
        service.viking_fs, ctx, UserSettingsPatch.model_validate({"privacy": {"mode": "auto"}})
    )
    await _add_skill(client, "privacy-update-skill", "Original description")

    privacy_response = await client.post(
        "/api/v1/privacy-configs/skill/privacy-update-skill",
        json={"values": {"api_key": "secret-1"}, "change_reason": "seed"},
    )
    assert privacy_response.status_code == 200, privacy_response.text

    reference = build_privacy_field_reference(
        ctx.user.user_id, "skill", "privacy-update-skill", "api_key"
    )
    session = await service.sessions.create(ctx, "privacy-history")
    await session.add_message_async("user", [TextPart(reference)])
    await patch_user_config(
        service.viking_fs, ctx, UserSettingsPatch.model_validate({"privacy": {"mode": "off"}})
    )
    update_response = await client.put(
        "/api/v1/skills/privacy-update-skill",
        json={
            "data": _skill_md(
                "privacy-update-skill",
                "Updated description",
                "No secret placeholders remain in this skill.",
            ),
            "wait": True,
        },
    )
    assert update_response.status_code == 200, update_response.text

    target = "/api/v1/privacy-configs/skill/privacy-update-skill"
    current = (await client.get(target)).json()["result"]["current"]
    assert current == privacy_response.json()["result"]
    assert (await client.get(target + "/versions/1")).status_code == 200
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx)
    assert json.loads(restored)["parts"][0]["text"] == "secret-1"
    response = await client.delete(target)
    assert response.status_code == 200 and response.json()["result"]["deleted"]
    assert (await client.get(target)).status_code == 404
    assert (await client.get(target + "/versions")).status_code == 404


async def test_skills_api_update_keeps_privacy_after_post_write_failure(
    client, service, monkeypatch
):
    from openviking.storage.queuefs import get_queue_manager
    from openviking.utils.skill_processor import SkillProcessor

    await _add_skill(client, "failed-privacy-write-skill", "Original description")
    queue_manager = get_queue_manager()
    queue = queue_manager.get_queue(queue_manager.SEMANTIC)
    enqueue = AsyncMock(wraps=queue.enqueue)
    monkeypatch.setattr(queue, "enqueue", enqueue)

    seeded_privacy = await client.post(
        "/api/v1/privacy-configs/skill/failed-privacy-write-skill",
        json={"values": {"api_key": "secret-old"}, "change_reason": "seed"},
    )
    assert seeded_privacy.status_code == 200, seeded_privacy.text

    await patch_user_config(
        service.viking_fs,
        RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
        UserSettingsPatch.model_validate({"privacy": {"mode": "auto"}}),
    )
    secret = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    original_apply = SkillProcessor.apply_skill_privacy

    async def _apply_then_fail(
        self,
        skill_dict,
        preparation,
        ctx,
        *,
        change_reason,
        owner_lease_ref=None,
        previous_versions=None,
    ):
        result = await original_apply(
            self,
            skill_dict,
            preparation,
            ctx,
            change_reason=change_reason,
            owner_lease_ref=owner_lease_ref,
            previous_versions=previous_versions,
        )
        if skill_dict.get("name") == "failed-privacy-write-skill":
            raise RuntimeError("privacy post-write failure")
        return result

    monkeypatch.setattr(SkillProcessor, "apply_skill_privacy", _apply_then_fail)

    response = await client.put(
        "/api/v1/skills/failed-privacy-write-skill",
        json={
            "data": _skill_md(
                "failed-privacy-write-skill",
                "Updated description",
                f'api_key: "{secret}"\n',
            ),
            "wait": True,
        },
    )
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "INTERNAL"
    enqueue.assert_not_awaited()

    privacy_response = await client.get("/api/v1/privacy-configs/skill/failed-privacy-write-skill")
    assert privacy_response.status_code == 200, privacy_response.text
    assert set(privacy_response.json()["result"]["current"]["values"].values()) == {
        "secret-old",
        secret,
    }

    show_response = await client.get(
        "/api/v1/skills/failed-privacy-write-skill",
        params={"include_content": True},
    )
    assert show_response.status_code == 200, show_response.text
    assert "Original description" in show_response.json()["result"]["content"]

    monkeypatch.setattr(SkillProcessor, "apply_skill_privacy", original_apply)
    retry = await client.put(
        "/api/v1/skills/failed-privacy-write-skill",
        json={"data": _skill_md("failed-privacy-write-skill", "Successful retry"), "wait": True},
    )
    assert retry.status_code == 200, retry.text
    shown = await client.get("/api/v1/skills/failed-privacy-write-skill")
    assert shown.json()["result"]["description"] == "Successful retry"


async def test_skills_api_rejects_invalid_skill_names(client):
    invalid_names = [
        "team/sql-helper",
        "bad name",
        ".",
        "..",
        "a" * 65,
    ]

    for name in invalid_names:
        response = await client.post(
            "/api/v1/skills",
            json={"data": _skill_md(name, "Invalid name skill"), "wait": True},
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_skills_api_show_reads_source_metadata_and_hides_internal_file(client):
    await _add_skill(client, "source-skill", "Source metadata skill")

    show_response = await client.get(
        "/api/v1/skills/source-skill",
        params={"include_files": True, "include_source": True},
    )
    assert show_response.status_code == 200, show_response.text
    shown = show_response.json()["result"]
    assert shown["source"]["tracked"] is True
    assert shown["source"]["type"] == "api"
    assert shown["source"]["source"] == "inline_content"
    assert shown["source"]["operation"] == "add"
    assert shown["source"]["skill_name"] == "source-skill"
    assert all(file["path"] != ".source.json" for file in shown["files"])


async def test_skills_api_add_accepts_source_metadata_override(client, monkeypatch, tmp_path):
    response = await client.post(
        "/api/v1/skills",
        json={
            "data": _skill_md("git-source-skill", "Git source skill"),
            "wait": True,
            "source_metadata": {
                "type": "git",
                "source": "https://github.com/anthropics/skills/tree/main/skills",
                "clone_url": "https://github.com/anthropics/skills.git",
                "ref_name": "main",
                "subdir": "skills/git-source-skill",
            },
        },
    )
    assert response.status_code == 200, response.text

    show_response = await client.get(
        "/api/v1/skills/git-source-skill",
        params={"include_source": True},
    )
    assert show_response.status_code == 200, show_response.text
    source = show_response.json()["result"]["source"]
    assert source["tracked"] is True
    assert source["type"] == "git"
    assert source["clone_url"] == "https://github.com/anthropics/skills.git"
    assert source["ref_name"] == "main"
    assert source["subdir"] == "skills/git-source-skill"
    assert source["skill_name"] == "git-source-skill"

    from openviking.parse.accessors.base import LocalResource, SourceType
    from openviking.parse.accessors.git_accessor import GitAccessor

    downloads = []

    async def download(_accessor, source, **kwargs):
        root = tmp_path / f"download-{len(downloads)}"
        for name in ("remote-a", "remote-b"):
            path = root / "skills" / name
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(_skill_md(name, f"Revision {len(downloads)}"))
            (path / "asset.bin").write_bytes(b"\x00\xff")
        downloads.append((root, source, kwargs.get("ref")))
        return LocalResource(path=root, source_type=SourceType.GIT, original_source=source)

    monkeypatch.setattr(GitAccessor, "access", download)
    url = "https://github.com/acme/skills/tree/feature/foo/skills"
    listing = await client.post("/api/v1/skills", json={"data": url, "list_only": True})
    assert listing.status_code == 200, listing.text
    assert {item["name"] for item in listing.json()["result"]["skills"]} == {"remote-a", "remote-b"}
    assert (await client.get("/api/v1/skills/remote-a")).status_code == 404
    assert downloads[-1][2] == "feature/foo"
    assert not downloads[-1][0].exists()

    selected = await client.post(
        "/api/v1/skills", json={"data": url, "skills": ["remote-a"], "wait": True}
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["result"]["name"] == "remote-a"
    assert (await client.get("/api/v1/skills/remote-b")).status_code == 404
    detail = (await client.get("/api/v1/skills/remote-a", params={"include_source": True})).json()[
        "result"
    ]
    assert detail["source"]["subdir"] == "skills/remote-a"
    assert detail["source"]["ref_name"] == "feature/foo"
    assert any(item["name"] == "asset.bin" for item in detail["files"])

    async def fail_source_write(*args, **kwargs):
        raise RuntimeError("source metadata write failed")

    with monkeypatch.context() as fault:
        fault.setattr(
            "openviking.server.skill_source_metadata.write_skill_source_metadata",
            fail_source_write,
        )
        failed = await client.put(
            "/api/v1/skills/remote-a", json={"from_source": True, "wait": True}
        )
    assert failed.status_code == 500, failed.text
    after_failure = (
        await client.get("/api/v1/skills/remote-a", params={"include_source": True})
    ).json()["result"]
    assert after_failure["source"] == detail["source"]
    assert after_failure["description"] != detail["description"]

    refreshed = await client.put(
        "/api/v1/skills/remote-a", json={"from_source": True, "wait": True}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["result"]["name"] == "remote-a"
    assert downloads[-1][2] == "feature/foo"

    batch = await client.post("/api/v1/skills", json={"data": url, "skills": ["*"]})
    assert batch.status_code == 200, batch.text
    installed = batch.json()["result"]["installed"]
    assert {item["name"] for item in installed} == {"remote-a", "remote-b"}
    assert len({item["task_id"] for item in installed}) == 2
    assert all(not root.exists() for root, _, _ in downloads)

    missing = await client.post("/api/v1/skills", json={"data": url, "skills": ["missing"]})
    assert missing.status_code == 400, missing.text
    assert not downloads[-1][0].exists()
    count = len(downloads)
    traversal = await client.post(
        "/api/v1/skills", json={"data": "https://github.com/acme/skills/tree/main/%2e%2e/outside"}
    )
    assert traversal.status_code == 400, traversal.text
    local = await client.post("/api/v1/skills", json={"data": str(tmp_path)})
    assert local.status_code == 403, local.text
    assert len(downloads) == count


async def test_skills_api_update_accepts_binary_auxiliary_files(client, service):
    from openviking.privacy.skill_placeholder import build_placeholder

    owner = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    reader = RequestContext(user=UserIdentifier(owner.account_id, "reader"), role=Role.USER)
    await patch_user_config(
        service.viking_fs, owner, UserSettingsPatch.model_validate({"privacy": {"mode": "auto"}})
    )
    name = "binary-skill"
    created = await client.post(
        "/api/v1/skills",
        json={
            "data": _skill_md(name, "Original binary skill"),
            "target_uri": "viking://agent/skills",
            "wait": True,
        },
    )
    assert created.status_code == 200, created.text
    original = "ghp_aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    rotated = "ghp_cD4fG6hJ8kM0nP2qR5sU7vW9xY1zA3bE"
    binary = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", _skill_md(name, "Binary skill", f'key="{original}"'))
        archive.writestr("preview.bin", binary)
    archive_bytes = buffer.getvalue()
    upload = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("binary-skill.zip", archive_bytes, "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    source = {
        "temp_file_id": upload.json()["result"]["temp_file_id"],
        "target_uri": "viking://agent/skills",
        "wait": True,
    }
    added = await client.put("/api/v1/skills/" + name, json=source)
    assert added.status_code == 200, added.text
    root = added.json()["result"]["root_uri"]
    current = await service.privacy_configs.get_current(owner, "skill", name)
    [field] = current.values
    await service.privacy_configs.patch_values(owner, "skill", name, {field: rotated})
    await service.privacy_configs.upsert(reader, "skill", name, {field: "MemberSetting42"})
    current = await service.privacy_configs.get_current(owner, "skill", name)
    stored = await service.viking_fs.read_file(root + "/SKILL.md", ctx=owner)

    upload = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("binary-skill.zip", archive_bytes, "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    source["temp_file_id"] = upload.json()["result"]["temp_file_id"]
    rejected = await client.put("/api/v1/skills/" + name, json=source)
    assert rejected.status_code == 409, rejected.text
    assert original not in rejected.text and rotated not in rejected.text
    assert await service.viking_fs.read_file(root + "/SKILL.md", ctx=owner) == stored
    download = await client.get("/api/v1/content/download", params={"uri": root + "/preview.bin"})
    assert download.status_code == 200 and download.content == binary

    replaced = await client.put(
        "/api/v1/skills/" + name,
        json={
            "data": _skill_md(
                name, "Updated without auxiliary files", build_placeholder(name, field)
            ),
            "target_uri": "viking://agent/skills",
            "wait": True,
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert (
        await client.get("/api/v1/content/download", params={"uri": root + "/preview.bin"})
    ).status_code == 404
    assert (await service.privacy_configs.get_current(owner, "skill", name)) == current
    assert (await service.privacy_configs.get_version(owner, "skill", name, 1)).values == {
        field: original
    }
    assert rotated in await service.fs.read(root + "/SKILL.md", ctx=owner)
    assert "MemberSetting42" in await service.fs.read(root + "/SKILL.md", ctx=reader)


async def test_skills_api_validate_inline_skill(client):
    valid_response = await client.post(
        "/api/v1/skills/validate",
        json={
            "data": _skill_md("valid-skill", "Valid skill"),
            "skill_dir_name": "valid-skill",
        },
    )
    assert valid_response.status_code == 200, valid_response.text
    valid = valid_response.json()["result"]
    assert valid["valid"] is True
    assert valid["name"] == "valid-skill"
    assert valid["errors"] == []
    assert valid["warnings"] == []

    invalid_response = await client.post(
        "/api/v1/skills/validate",
        json={"data": "# Missing frontmatter"},
    )
    assert invalid_response.status_code == 200, invalid_response.text
    invalid = invalid_response.json()["result"]
    assert invalid["valid"] is False
    assert invalid["errors"]

    missing_description_response = await client.post(
        "/api/v1/skills/validate",
        json={"data": "---\nname: missing-description\n---\n# Body"},
    )
    assert missing_description_response.status_code == 200, missing_description_response.text
    missing_description = missing_description_response.json()["result"]
    assert missing_description["valid"] is False
    assert any(issue["rule"] == "description_required" for issue in missing_description["errors"])


async def test_skills_api_validate_rfc_strict_and_loose_rules(client):
    mismatch = _skill_md("actual-name", "Valid description")

    loose_response = await client.post(
        "/api/v1/skills/validate",
        json={"data": mismatch, "skill_dir_name": "directory-name"},
    )
    assert loose_response.status_code == 200, loose_response.text
    loose = loose_response.json()["result"]
    assert loose["valid"] is True
    assert loose["errors"] == []
    assert any(issue["rule"] == "name_matches_directory" for issue in loose["warnings"])

    strict_response = await client.post(
        "/api/v1/skills/validate",
        json={"data": mismatch, "skill_dir_name": "directory-name", "strict": True},
    )
    assert strict_response.status_code == 200, strict_response.text
    strict = strict_response.json()["result"]
    assert strict["valid"] is False
    assert any(issue["rule"] == "name_matches_directory" for issue in strict["errors"])

    long_body = "\n".join(f"line {idx}" for idx in range(501))
    long_body_response = await client.post(
        "/api/v1/skills/validate",
        json={
            "data": _skill_md("long-body", "Valid description", long_body),
            "skill_dir_name": "long-body",
            "strict": True,
        },
    )
    assert long_body_response.status_code == 200, long_body_response.text
    long_body_result = long_body_response.json()["result"]
    assert long_body_result["valid"] is True
    assert any(issue["rule"] == "body_max_lines" for issue in long_body_result["warnings"])


async def test_skill_package_indexes_nested_content_and_returns_actual_hit(
    client, service, tmp_path
):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.expr import Eq
    from openviking_cli.session.user_id import UserIdentifier

    archive = tmp_path / "package-search.zip"
    with zipfile.ZipFile(archive, "w") as package:
        skill = _skill_md("package-search", "Root skill description")
        package.writestr(
            "SKILL.md", skill.replace("tags:\n", "allowed-tools: [Read, Bash]\ntags:\n")
        )
        package.writestr("reference/nested/recovery.md", "Restore a backup in another region.")
        package.writestr("reference/nested/recovery-copy.md", "Restore a backup in another region.")
        package.writestr("reference/SKILL.md", _skill_md("attachment", "An ordinary attachment"))
    with archive.open("rb") as handle:
        uploaded = await client.post(
            "/api/v1/resources/temp_upload",
            files={"file": (archive.name, handle, "application/zip")},
        )
    assert uploaded.status_code == 200, uploaded.text
    added = await client.post(
        "/api/v1/skills",
        json={"temp_file_id": uploaded.json()["result"]["temp_file_id"], "wait": True},
    )
    assert added.status_code == 200, added.text
    root = added.json()["result"]["root_uri"]
    # Both root summary levels retain the package identity in the stored index.
    records = await service.vikingdb_manager.filter(
        filter=Eq("uri", root),
        output_fields=["level", "name", "description"],
        ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
    )
    assert {record["level"] for record in records} == {0, 1}
    for record in records:
        assert record["name"] == "package-search"
        assert record["description"] == "Root skill description"
    for directory in (root, f"{root}/reference", f"{root}/reference/nested"):
        for endpoint in ("abstract", "overview"):
            content = await client.get(f"/api/v1/content/{endpoint}", params={"uri": directory})
            assert content.status_code == 200, content.text
            assert content.json()["result"]

    for endpoint in ("/api/v1/skills/find", "/api/v1/search/find"):
        found = await client.post(
            endpoint,
            json={
                "query": "backup recovery",
                "target_uri": f"{root}/reference/nested",
                "level": [2],
                "limit": 10,
            },
        )
        assert found.status_code == 200, found.text
        hits = found.json()["result"]["skills"]
        assert len(hits) == (1 if endpoint == "/api/v1/skills/find" else 2)
        expected_uris = {
            f"{root}/reference/nested/recovery.md",
            f"{root}/reference/nested/recovery-copy.md",
        }
        assert all(hit["uri"] in expected_uris and hit["level"] == 2 for hit in hits)
        if endpoint == "/api/v1/skills/find":
            assert hits[0]["root_uri"] == root
            assert hits[0]["skill_md_uri"] == f"{root}/SKILL.md"
            assert hits[0]["description"] == "Root skill description"
            assert hits[0]["tags"] == ["test"]
            assert hits[0]["allowed_tools"] == ["Read", "Bash"]

    listed = await client.get("/api/v1/skills")
    assert [skill["name"] for skill in listed.json()["result"]["skills"]] == ["package-search"]
    deleted = await client.delete("/api/v1/skills/package-search")
    assert deleted.status_code == 200, deleted.text
    found = await client.post("/api/v1/skills/find", json={"query": "backup recovery"})
    assert found.json()["result"]["skills"] == []


async def test_mcp_skill_import_and_vector_rebuild_preserve_l1_frontmatter(client, service):
    from openviking.core.mcp_converter import mcp_to_skill
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    tool = {
        "name": "review_calculator",
        "description": "Perform mathematical calculations",
        "inputSchema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "Expression"}},
            "required": ["expression"],
        },
    }
    added = await client.post("/api/v1/skills", json={"data": tool, "wait": True})
    assert added.status_code == 200, added.text
    root = added.json()["result"]["root_uri"]
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    expected_body = mcp_to_skill(tool)["content"].strip()

    async def assert_indexed_body():
        for level in (0, 1):
            records = await service.vikingdb_manager.filter_in_tenant(
                ctx=ctx, target_directories=[root], level=[level]
            )
            assert len(records) == 1
            if level == 1:
                assert records[0]["abstract"].strip() == expected_body
        records = await service.vikingdb_manager.get_context_by_uri(
            f"{root}/SKILL.md", level=2, ctx=ctx
        )
        assert len(records) == 1

    await assert_indexed_body()
    rebuilt = await client.post(
        "/api/v1/content/reindex",
        json={"uri": root, "mode": "vectors_only", "wait": True},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["result"]["status"] == "completed"
    assert rebuilt.json()["result"]["failed_records"] == 0
    assert rebuilt.json()["result"]["rebuilt_records"] == 3
    await assert_indexed_body()
