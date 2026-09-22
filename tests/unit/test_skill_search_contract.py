# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Skill router contracts using grouped search results and no running server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.routers import skills as skills_router


def _grouped_hit(root_uri, score=0.91):
    return {
        "uri": root_uri + "/reference/nested/backup.md",
        "level": 2,
        "context_type": "skill",
        "score": score,
        "abstract": "name: file-name\ndescription: File-only description\ntags: [file-only]",
    }


@pytest.fixture
def fake_search(monkeypatch):
    async def root_abstract(uri, *, ctx):
        name = uri.rsplit("/", 1)[-1]
        return (
            f"name: {name}\n"
            "description: Root skill description\n"
            "tags: [backup, production]\n"
            "allowed_tools: [Read, Bash]\n"
        )

    find = AsyncMock()
    abstract = AsyncMock(side_effect=root_abstract)
    service = SimpleNamespace(
        search=SimpleNamespace(find_skills=find),
        fs=SimpleNamespace(abstract=abstract),
    )

    async def run_operation(*, operation, telemetry, fn):
        return SimpleNamespace(result=await fn(), telemetry=None)

    monkeypatch.setattr(skills_router, "get_service", lambda: service)
    monkeypatch.setattr(skills_router, "run_operation", run_operation)
    return SimpleNamespace(find=find, abstract=abstract)


async def test_find_skills_applies_one_limit_across_same_named_skills_in_both_spaces(
    fake_search, request_context
):
    user_root = f"viking://user/{request_context.user.user_id}/skills"
    agent_root = "viking://agent/skills"
    user_hit = _grouped_hit(f"{user_root}/data.service", 0.89)
    agent_hit = _grouped_hit(f"{agent_root}/data.service", 0.94)

    async def find(**kwargs):
        if kwargs["target_uri"] == user_root:
            return {"skills": [user_hit, _grouped_hit(f"{user_root}/other", 0.71)]}
        assert kwargs["target_uri"] == agent_root
        return {"skills": [agent_hit, _grouped_hit(f"{agent_root}/another", 0.74)]}

    fake_search.find.side_effect = find

    response = await skills_router.find_skills(
        skills_router.FindSkillsRequest(query="backup", limit=2),
        _ctx=request_context,
    )

    result = response["result"]
    assert result["total"] == 2
    assert [hit["name"] for hit in result["skills"]] == ["data.service", "data.service"]
    assert [hit["root_uri"] for hit in result["skills"]] == [
        f"{agent_root}/data.service",
        f"{user_root}/data.service",
    ]
    assert [hit["uri"] for hit in result["skills"]] == [agent_hit["uri"], user_hit["uri"]]
