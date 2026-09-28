# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""What SearchService.find_skills hands the package retriever."""

from types import SimpleNamespace

import pytest

from openviking.retrieve import skill_package_retriever
from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

SKILLS = "viking://agent/skills"
TAG_FILTER = {"op": "must", "field": "search_tags", "conds": ["team=infra"]}


def _ctx():
    return RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.USER)


@pytest.fixture
def seen(monkeypatch):
    captured = {}

    class _Retriever:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def retrieve_skills(self, query, ctx, **kwargs):
            captured["retrieve"] = kwargs
            return SimpleNamespace(matched_contexts=[])

    monkeypatch.setattr(skill_package_retriever, "SkillPackageRetriever", _Retriever)
    return captured


def _service(rerank_config):
    async def ensure_scope(target, ctx):
        return None

    fs = SimpleNamespace(
        _ensure_retrieval_scope=ensure_scope,
        _get_vector_store=lambda: object(),
        _get_embedder=lambda: object(),
        rerank_config=rerank_config,
        retrieval_config=None,
    )
    return SearchService(fs)


async def test_find_skills_applies_the_configured_rerank_threshold(seen):
    """Without the rerank config the package floor is 0 while general find sits at 0.1."""
    rerank_config = SimpleNamespace(threshold=0.1)

    await _service(rerank_config).find_skills(query="deploy", ctx=_ctx(), target_uri=SKILLS)

    assert seen["init"]["rerank_config"] is rerank_config


async def test_find_skills_forwards_the_filter_as_the_retrieval_scope(seen):
    await _service(None).find_skills(
        query="deploy", ctx=_ctx(), target_uri=SKILLS, filter=TAG_FILTER
    )

    assert seen["retrieve"]["scope_dsl"] == TAG_FILTER


@pytest.mark.parametrize("query", ["", "  "])
async def test_find_skills_needs_a_query_even_with_a_filter(seen, query):
    """It embeds the query, so a filter cannot stand in the way it can on find."""
    with pytest.raises(InvalidArgumentError):
        await _service(None).find_skills(
            query=query, ctx=_ctx(), target_uri=SKILLS, filter=TAG_FILTER
        )

    assert seen == {}
