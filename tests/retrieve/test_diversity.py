# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Dict, List

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.diversity import diversity_group_key, select_diverse_contexts
from openviking_cli.retrieve.diversity import DiversityOptions
from openviking_cli.retrieve.types import ContextType, MatchedContext


class FakeEmbedder:
    def __init__(self, vectors: Dict[str, List[float]], fail: bool = False):
        self.vectors = vectors
        self.fail = fail
        self.calls = 0

    def prepare_embedding_input(self, content: str) -> str:
        return content

    async def embed_async(self, content: str, *, is_query: bool = False) -> EmbedResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("fake embedding failure")
        return EmbedResult(dense_vector=self.vectors[content])


def _context(uri: str, score: float, abstract: str) -> MatchedContext:
    return MatchedContext(
        uri=uri,
        context_type=ContextType.RESOURCE,
        score=score,
        abstract=abstract,
    )


def test_diversity_group_key_normalizes_level_and_group_modes():
    uri = "viking://resources/project/docs/page.abstract.md"
    assert diversity_group_key(uri, "parent") == "viking://resources/project/docs"
    assert diversity_group_key(uri, "source_root") == "viking://resources/project"
    assert (
        diversity_group_key("viking://user/alice/resources/project/a.md", "source_root")
        == "viking://user/alice/resources/project"
    )
    assert (
        diversity_group_key("viking://user/alice/peers/agent/resources/shared/a.md", "source_root")
        == "viking://user/alice/peers/agent/resources/shared"
    )
    assert (
        diversity_group_key("viking://agent/skills/reviewer/SKILL.md", "source_root")
        == "viking://agent/skills/reviewer"
    )


@pytest.mark.asyncio
async def test_select_diverse_contexts_combines_mmr_group_cap_and_explanations():
    candidates = [
        _context("viking://resources/a/one.md", 0.95, "same topic"),
        _context("viking://resources/a/two.md", 0.94, "same topic copy"),
        _context("viking://resources/b/three.md", 0.82, "different topic"),
    ]
    embedder = FakeEmbedder(
        {
            "same topic": [1.0, 0.0],
            "same topic copy": [0.99, 0.01],
            "different topic": [0.0, 1.0],
        }
    )
    selection = await select_diverse_contexts(
        candidates,
        options=DiversityOptions(
            strategy="combined",
            max_per_group=1,
            similarity_threshold=0.97,
        ),
        embedder=embedder,
        limit=2,
    )
    assert [item.uri for item in selection.contexts] == [
        "viking://resources/a/one.md",
        "viking://resources/b/three.md",
    ]
    assert selection.contexts[0].deduplicated_from == ["viking://resources/a/two.md"]


@pytest.mark.asyncio
async def test_exact_abstract_fold_does_not_call_embedder_for_group_limit():
    embedder = FakeEmbedder({})
    selection = await select_diverse_contexts(
        [
            _context("viking://resources/a.md", 0.9, " Same\ncontent "),
            _context("viking://resources/b.md", 0.8, "same content"),
        ],
        options=DiversityOptions(strategy="group_limit", max_per_group=2),
        embedder=embedder,
        limit=2,
    )
    assert [item.uri for item in selection.contexts] == ["viking://resources/a.md"]
    assert selection.contexts[0].deduplicated_from == ["viking://resources/b.md"]
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_without_raising():
    candidates = [
        _context("viking://resources/a/one.md", 0.9, "one"),
        _context("viking://resources/b/two.md", 0.8, "two"),
    ]
    selection = await select_diverse_contexts(
        candidates,
        options=DiversityOptions(strategy="combined", max_per_group=1),
        embedder=FakeEmbedder({}, fail=True),
        limit=2,
    )
    assert [item.uri for item in selection.contexts] == [item.uri for item in candidates]
    assert selection.fallback_used is True


@pytest.mark.asyncio
async def test_limit_zero_returns_without_embedding():
    embedder = FakeEmbedder({"one": [1.0]})
    selection = await select_diverse_contexts(
        [_context("viking://resources/a.md", 1.0, "one")],
        options=DiversityOptions(strategy="mmr"),
        embedder=embedder,
        limit=0,
    )
    assert selection.contexts == []
    assert embedder.calls == 0
