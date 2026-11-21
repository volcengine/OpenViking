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
    selected_roots = {f"{user_root}/data.service", f"{agent_root}/data.service"}
    root_abstract = fake_search.abstract.side_effect

    async def selected_abstract(uri, *, ctx):
        if uri not in selected_roots:
            raise RuntimeError("Unselected skill abstract is unavailable")
        return await root_abstract(uri, ctx=ctx)

    async def find(**kwargs):
        if kwargs["target_uri"] == user_root:
            return {"skills": [user_hit, _grouped_hit(f"{user_root}/other", 0.71)]}
        assert kwargs["target_uri"] == agent_root
        return {"skills": [agent_hit, _grouped_hit(f"{agent_root}/another", 0.74)]}

    fake_search.find.side_effect = find
    fake_search.abstract.side_effect = selected_abstract

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
    assert [hit["score"] for hit in result["skills"]] == [0.94, 0.89]
    assert [hit["abstract"] for hit in result["skills"]] == [
        agent_hit["abstract"],
        user_hit["abstract"],
    ]
    assert all(hit["level"] == 2 for hit in result["skills"])
    assert fake_search.abstract.await_count == 2
    assert {call.args[0] for call in fake_search.abstract.await_args_list} == selected_roots


async def test_find_skills_breaks_score_ties_by_package_root(fake_search, request_context):
    user_root = f"viking://user/{request_context.user.user_id}/skills"
    roots = [f"{user_root}/backup", f"{user_root}/backup.extra"]

    async def find(**kwargs):
        if kwargs["target_uri"] == user_root:
            return {"skills": [_grouped_hit(root) for root in reversed(roots)]}
        return {"skills": [_grouped_hit("viking://agent/skills/shared", 0.5)]}

    fake_search.find.side_effect = find
    response = await skills_router.find_skills(
        skills_router.FindSkillsRequest(query="backup", limit=2), _ctx=request_context
    )

    assert [hit["root_uri"] for hit in response["result"]["skills"]] == roots
    assert fake_search.abstract.await_count == 2


async def test_find_skills_propagates_selected_abstract_failure(fake_search, request_context):
    user_root = f"viking://user/{request_context.user.user_id}/skills"
    selected_root = f"{user_root}/backup"

    async def find(**kwargs):
        if kwargs["target_uri"] == user_root:
            return {"skills": [_grouped_hit(selected_root)]}
        return {"skills": [_grouped_hit("viking://agent/skills/shared", 0.5)]}

    fake_search.find.side_effect = find
    fake_search.abstract.side_effect = RuntimeError("Selected skill abstract is unavailable")

    with pytest.raises(RuntimeError, match="Selected skill abstract is unavailable"):
        await skills_router.find_skills(
            skills_router.FindSkillsRequest(query="backup", limit=1), _ctx=request_context
        )

    fake_search.abstract.assert_awaited_once_with(selected_root, ctx=request_context)
