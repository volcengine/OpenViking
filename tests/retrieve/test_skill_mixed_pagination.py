# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""General search keeps item results; package grouping belongs to the Skills API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking_cli.retrieve.types import TypedQuery
from tests.retrieve.test_skill_package_results import SKILLS, PagedStore, ctx, row


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
