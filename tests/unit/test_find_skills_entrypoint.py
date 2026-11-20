# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Service contracts for package retrieval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

SKILLS = "viking://agent/skills"
TAG_FILTER = {"op": "must", "field": "search_tags", "conds": ["team=infra"]}


def _ctx():
    return RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.USER)


async def test_find_skills_uses_keyword_recall_without_an_embedder():
    uri = f"{SKILLS}/deploy/scripts/rollback.md"
    recall = AsyncMock(
        return_value=[
            {"uri": uri, "context_type": "skill", "_score": 0.05, "abstract": "Rollback steps"}
        ],
    )
    store = SimpleNamespace(
        search_by_keywords_in_tenant=recall,
        get_account_backend=AsyncMock(
            return_value=SimpleNamespace(collection_exists=AsyncMock(return_value=True))
        ),
    )
    fs = SimpleNamespace(
        _ensure_retrieval_scope=AsyncMock(),
        _get_vector_store=lambda: store,
        _get_embedder=lambda _ctx: None,
        stat=AsyncMock(return_value={"isDir": True}),
        rerank_config=SimpleNamespace(threshold=0.5),
    )

    result = await SearchService(fs).find_skills(
        query="rollback",
        ctx=_ctx(),
        target_uri=SKILLS,
        filter=TAG_FILTER,
        search_type="keywords",
    )

    assert [(hit.uri, hit.score, hit.abstract) for hit in result.skills] == [
        (uri, 0.05, "Rollback steps")
    ]
    assert recall.await_args.kwargs["query"] == "rollback"
    assert recall.await_args.kwargs["extra_filter"] == TAG_FILTER
    assert recall.await_args.kwargs["target_directories"] == [SKILLS]


@pytest.mark.parametrize("query", ["", "  ", "broken\ud835"])
async def test_find_skills_needs_a_valid_query_even_with_a_filter(query):
    with pytest.raises(InvalidArgumentError):
        await SearchService().find_skills(
            query=query, ctx=_ctx(), target_uri=SKILLS, filter=TAG_FILTER
        )
