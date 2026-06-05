# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import sys
import types
import zipfile

import pytest
from starlette.responses import PlainTextResponse


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


async def test_skills_api_list_show_find_and_delete(client):
    added = await _add_skill(client, "api-skill", "API skill for list and show")
    assert added["uri"].endswith("/skills/api-skill")

    list_response = await client.get("/api/v1/skills")
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()["result"]
    assert listed["total"] >= 1
    assert any(skill["name"] == "api-skill" for skill in listed["skills"])

    show_response = await client.get(
        "/api/v1/skills/api-skill",
        params={"level": 2, "include_files": True, "include_source": True},
    )
    assert show_response.status_code == 200, show_response.text
    shown = show_response.json()["result"]
    assert shown["name"] == "api-skill"
    assert shown["description"] == "API skill for list and show"
    assert shown["skill_md_uri"].endswith("/skills/api-skill/SKILL.md")
    assert "# api-skill" in shown["content"]
    assert any(file["name"] == "SKILL.md" for file in shown["files"])
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

    missing_response = await client.get("/api/v1/skills/api-skill")
    assert missing_response.status_code == 404


async def test_skills_api_update_requires_matching_name(client):
    await _add_skill(client, "update-skill", "Original description")

    mismatch_response = await client.put(
        "/api/v1/skills/update-skill",
        json={"data": _skill_md("other-skill", "Wrong name"), "wait": True},
    )
    assert mismatch_response.status_code == 400
    assert mismatch_response.json()["error"]["code"] == "INVALID_ARGUMENT"

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


async def test_skills_api_show_reads_source_metadata_and_hides_internal_file(client):
    added = await _add_skill(client, "source-skill", "Source metadata skill")
    metadata_uri = f"{added['root_uri']}/.source.json"

    write_response = await client.post(
        "/api/v1/content/write",
        json={
            "uri": metadata_uri,
            "content": (
                '{"type":"git","clone_url":"https://github.com/acme/skills.git",'
                '"ref_name":"main","subdir":"skills/source-skill","skill_name":"source-skill"}'
            ),
            "mode": "replace",
            "wait": True,
        },
    )
    assert write_response.status_code == 200, write_response.text

    show_response = await client.get(
        "/api/v1/skills/source-skill",
        params={"include_files": True, "include_source": True},
    )
    assert show_response.status_code == 200, show_response.text
    shown = show_response.json()["result"]
    assert shown["source"]["tracked"] is True
    assert shown["source"]["type"] == "git"
    assert shown["source"]["clone_url"] == "https://github.com/acme/skills.git"
    assert shown["source"]["subdir"] == "skills/source-skill"
    assert all(file["path"] != ".source.json" for file in shown["files"])


async def test_skills_api_update_accepts_binary_auxiliary_files(client, tmp_path):
    await _add_skill(client, "binary-skill", "Original binary skill")

    skill_dir = tmp_path / "binary-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        _skill_md("binary-skill", "Updated binary skill"),
        encoding="utf-8",
    )
    (skill_dir / "preview.bin").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01")

    archive = tmp_path / "binary-skill.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        for path in skill_dir.rglob("*"):
            zip_file.write(path, path.relative_to(skill_dir).as_posix())

    with archive.open("rb") as handle:
        upload_response = await client.post(
            "/api/v1/resources/temp_upload",
            files={"file": ("binary-skill.zip", handle, "application/zip")},
        )
    assert upload_response.status_code == 200, upload_response.text
    temp_file_id = upload_response.json()["result"]["temp_file_id"]

    update_response = await client.put(
        "/api/v1/skills/binary-skill",
        json={"temp_file_id": temp_file_id, "wait": True},
    )
    assert update_response.status_code == 200, update_response.text

    download_response = await client.get(
        "/api/v1/content/download",
        params={"uri": "viking://agent/default/skills/binary-skill/preview.bin"},
    )
    assert download_response.status_code == 200, download_response.text
    assert download_response.content.startswith(b"\xff\xd8\xff\xe0")


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
