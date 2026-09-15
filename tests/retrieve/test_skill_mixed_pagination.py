# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""General search keeps item results; package grouping belongs to the Skills API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.retrieve.skill_package_retriever import SkillPackageRetriever
from openviking.retrieve.skill_results import SkillResultResolver
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking_cli.retrieve.types import TypedQuery
from tests.retrieve.test_skill_package_results import SKILLS, Files, PagedStore, ctx, query, row


@pytest.mark.parametrize("route", ["quick", "thinking", "filter"])
async def test_general_search_preserves_multiple_hits_from_one_skill(route):
    records = [row(f"{SKILLS}/a/{i}.md", 0.99 - i / 1000) for i in range(12)]
    records.append(row("viking://resources/doc.md", 0.8, kind="resource"))
    store = PagedStore(records)
    if route == "filter":
        fs = SimpleNamespace(_get_vector_store=lambda: store, _ensure_retrieval_scope=AsyncMock())
        result = await _SemanticMixin._find_by_filter(
            fs, {"op": "must", "field": "search_tags", "conds": ["team=x"]}, ctx(), [], 2
        )
        matches = result.skills
    else:
        # Thinking obtains files through the ACL-aware global leaf query.
        store._acl_enabled = lambda ctx: True
        result = await HierarchicalRetriever(store, None).retrieve(
            TypedQuery("hello", None, ""),
            ctx(),
            limit=2,
            mode=RetrieverMode.THINKING if route == "thinking" else RetrieverMode.QUICK,
        )
        matches = result.matched_contexts
    assert [item.uri for item in matches] == [f"{SKILLS}/a/0.md", f"{SKILLS}/a/1.md"]
    assert all(call.get("offset", 0) == 0 for call in store.calls)


async def test_skill_only_pages_exclude_backups_and_do_not_admit_other_types():
    records = [row(f"{SKILLS}/.demo.update-backup-123/{i}.md", 0.99) for i in range(10)]
    records.extend(
        [
            row(f"{SKILLS}/demo/.rules.md", 0.8),
            row("viking://user/user1/skills/demo/reference/.hidden.md", 0.7),
            row("viking://resources/doc.md", 0.9, kind="resource"),
        ]
    )
    store, files = PagedStore(records), Files()
    result = await SkillPackageRetriever(store, None).retrieve_skills(
        TypedQuery("hello", None, ""),
        ctx(),
        limit=2,
        skill_resolver=SkillResultResolver(files, ctx()),
    )
    assert [item.uri for item in result.matched_contexts] == [
        f"{SKILLS}/demo/.rules.md",
        "viking://user/user1/skills/demo/reference/.hidden.md",
    ]
    assert [call["offset"] for call in store.calls] == [0, 10]
    assert all(call["context_type"] == "skill" for call in store.calls)


async def test_skill_repeated_page_reports_incomplete_results():
    class RepeatingStore(PagedStore):
        def _page(self, records, kwargs):
            return super()._page(records, {**kwargs, "offset": 0})

    store = RepeatingStore([row(f"{SKILLS}/a/{i}.md", 0.9) for i in range(11)])
    with pytest.raises(RuntimeError, match="pagination did not advance"):
        await SkillPackageRetriever(store, None).retrieve_skills(
            query(), ctx(), limit=2, skill_resolver=SkillResultResolver(Files(), ctx())
        )
    assert [call["offset"] for call in store.calls] == [0, 10]
