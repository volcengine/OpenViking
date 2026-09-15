# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Skill router contracts using grouped search results and no running server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.routers import search as search_router
from openviking.server.routers import skills as skills_router
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.retrieve.types import FindResult


def _grouped_hit(root_uri, score=0.91, level=2):
    suffix = {
        0: "/.abstract.md",
        1: "/reference/nested/.overview.md",
        2: "/reference/nested/backup.md",
    }[level]
    return {
        "uri": root_uri + suffix,
        "level": level,
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
    read_visible = AsyncMock(side_effect=lambda uri, **kwargs: f"Visible content: {uri}")
    service = SimpleNamespace(
        search=SimpleNamespace(find=find, search=find, find_skills=find),
        fs=SimpleNamespace(abstract=abstract, read_visible=read_visible),
    )

    async def run_operation(*, operation, telemetry, fn):
        return SimpleNamespace(result=await fn(), telemetry=None)

    for router in (skills_router, search_router):
        monkeypatch.setattr(router, "get_service", lambda: service)
        monkeypatch.setattr(router, "run_operation", run_operation)
    return SimpleNamespace(find=find, abstract=abstract, read_visible=read_visible)


@pytest.mark.parametrize(("as_find_result", "level"), [(False, 0), (True, 1), (True, 2)])
async def test_find_skills_reads_root_identity_and_preserves_actual_hit(
    fake_search, request_context, as_find_result, level
):
    root_uri = "viking://agent/skills/data.service"
    hit = _grouped_hit(root_uri, level=level)
    payload = {"skills": [hit]}
    fake_search.find.return_value = FindResult.from_dict(payload) if as_find_result else payload
    target_uri = "viking://agent/skills"

    response = await skills_router.find_skills(
        skills_router.FindSkillsRequest(query="backup", target_uri=target_uri, level=[level]),
        _ctx=request_context,
    )

    assert response["status"] == "ok"
    assert response["result"]["total"] == 1
    result = response["result"]["skills"][0]
    assert result["root_uri"] == root_uri
    assert result["skill_md_uri"] == f"{root_uri}/SKILL.md"
    assert result["name"] == "data.service"
    assert result["description"] == "Root skill description"
    assert result["tags"] == ["backup", "production"]
    assert result["allowed_tools"] == ["Read", "Bash"]
    for field in ("uri", "level", "score", "abstract"):
        assert result[field] == hit[field]
    assert "best_match" not in result
    fake_search.abstract.assert_awaited_once_with(root_uri, ctx=request_context)
    assert fake_search.find.await_args.kwargs["target_uri"] == target_uri
    assert fake_search.find.await_args.kwargs["level"] == [level]


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
    assert {call.kwargs["target_uri"] for call in fake_search.find.await_args_list} == {
        user_root,
        agent_root,
    }


async def test_find_skills_does_not_use_hit_metadata_when_root_access_fails(
    fake_search, request_context
):
    fake_search.find.return_value = {"skills": [_grouped_hit("viking://agent/skills/backup")]}
    fake_search.abstract.side_effect = PermissionDeniedError("Root access denied")

    with pytest.raises(PermissionDeniedError):
        await skills_router.find_skills(
            skills_router.FindSkillsRequest(query="backup", target_uri="viking://agent/skills"),
            _ctx=request_context,
        )


@pytest.mark.parametrize(
    ("endpoint", "level", "read_content"),
    [
        ("find", 0, True),
        ("find", 2, False),
        ("search", 1, True),
        ("search", 2, True),
        ("search", 0, False),
    ],
)
async def test_generic_search_read_content_uses_actual_skill_hit_uri(
    fake_search, request_context, endpoint, level, read_content
):
    hit = _grouped_hit("viking://agent/skills/backup", level=level)
    fake_search.find.return_value = FindResult.from_dict({"skills": [hit]})
    request_type = search_router.FindRequest if endpoint == "find" else search_router.SearchRequest

    response = await getattr(search_router, endpoint)(
        request_type(query="backup", read_content=read_content, level=[level]),
        _ctx=request_context,
    )

    result = response["result"]["skills"][0]
    for field in ("uri", "level", "score", "abstract"):
        assert result[field] == hit[field]
    assert "best_match" not in result
    if read_content:
        fake_search.read_visible.assert_awaited_once_with(hit["uri"], ctx=request_context)
        assert result["content"] == f"Visible content: {hit['uri']}"
    else:
        fake_search.read_visible.assert_not_awaited()
        assert "content" not in result
    fake_search.abstract.assert_not_awaited()


@pytest.mark.parametrize(
    ("root_uri", "suffix"),
    [
        ("viking://agent/skills/data.service", ""),
        ("viking://user/alice/skills/data.service", "/"),
    ],
)
def test_skill_root_resolution_keeps_dotted_package_name(root_uri, suffix):
    assert skills_router._skill_root_from_hit_uri(root_uri + suffix) == root_uri
