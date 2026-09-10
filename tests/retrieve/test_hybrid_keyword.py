# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for keyword/dense fusion in find/search."""

import pytest

from openviking.core.context import ContextType
from openviking.retrieve.hybrid_keyword import HybridKeywordRecaller
from openviking.server.identity import RequestContext, Role
from openviking.storage.keywordfs.keyword_fs import KeywordFS
from openviking_cli.retrieve.types import MatchedContext
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.keyword_config import HybridRetrievalConfig, KeywordConfig

ACCOUNT = "default"


def _ctx():
    return RequestContext(user=UserIdentifier(account_id=ACCOUNT, user_id="alice"), role=Role.ROOT)


def _seed(kfs):
    kfs.upsert(ACCOUNT, "viking://resources/proj/B.md", "openviking rollback runbook", level=2, context_type="resource")
    kfs.upsert(ACCOUNT, "viking://resources/proj/C.md", "token version 2.4.1 rollback", level=2, context_type="resource")
    kfs.upsert(ACCOUNT, "viking://resources/proj/D.md", "unrelated", level=2, context_type="resource")


@pytest.fixture
def kfs(tmp_path):
    fs = KeywordFS(tmp_path, KeywordConfig(enabled=True, cjk_mode="char"))
    _seed(fs)
    return fs


def _dense():
    return [
        MatchedContext(uri="viking://resources/proj/A.md", context_type=ContextType.RESOURCE, level=2, score=0.9),
        MatchedContext(uri="viking://resources/proj/B.md", context_type=ContextType.RESOURCE, level=2, score=0.7),
    ]


async def _read_abstract(uri):
    return f"abstract of {uri.rsplit('/', 1)[-1]}"


@pytest.mark.asyncio
async def test_rrf_includes_keyword_only_hit(kfs):
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=True, fusion="rrf"), KeywordConfig(enabled=True))
    assert rec.enabled(_ctx())
    out = await rec.enhance("rollback 2.4.1", _dense(), ["viking://resources/proj"], _ctx(), limit=10, read_abstract=_read_abstract)
    uris = [m.uri for m in out]
    assert "viking://resources/proj/C.md" in uris, uris  # keyword-only exact-token hit
    assert "viking://resources/proj/D.md" not in uris, uris
    # keyword-only hit is abstract-enriched
    c = next(m for m in out if m.uri.endswith("C.md"))
    assert c.abstract and "abstract" in c.abstract


@pytest.mark.asyncio
async def test_weighted_fusion_includes_keyword_hit(kfs):
    rec = HybridKeywordRecaller(
        kfs,
        HybridRetrievalConfig(enabled=True, fusion="weighted", keyword_weight=0.5),
        KeywordConfig(enabled=True),
    )
    out = await rec.enhance("rollback 2.4.1", _dense(), ["viking://resources/proj"], _ctx(), limit=10, read_abstract=_read_abstract)
    uris = [m.uri for m in out]
    assert "viking://resources/proj/C.md" in uris, uris


@pytest.mark.asyncio
async def test_config_switch_is_reported_but_enhance_only_needs_a_sidecar(kfs):
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=False), KeywordConfig(enabled=True))
    assert rec.enabled(_ctx()) is False
    assert rec.sidecar_usable(_ctx()) is True
    out = await rec.enhance("rollback", _dense(), ["viking://resources/proj"], _ctx(), limit=10)
    assert out


@pytest.mark.asyncio
async def test_no_keyword_hit_returns_dense(kfs):
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=True), KeywordConfig(enabled=True))
    out = await rec.enhance("qqqq zzzz", _dense(), ["viking://resources/proj"], _ctx(), limit=10)
    assert [m.uri for m in out] == ["viking://resources/proj/A.md", "viking://resources/proj/B.md"]


def test_weighted_fusion_prefers_stronger_bm25(kfs):
    rec = HybridKeywordRecaller(
        kfs,
        HybridRetrievalConfig(enabled=True, fusion="weighted", keyword_weight=0.5),
        KeywordConfig(enabled=True),
    )
    # FTS5 bm25 scores are negative: -10 is a stronger match than -1.
    fused = rec._fuse_weighted(
        [],
        [("viking://resources/strong.md", -10.0), ("viking://resources/weak.md", -1.0)],
        limit=2,
    )
    assert [m.uri for m in fused] == [
        "viking://resources/strong.md",
        "viking://resources/weak.md",
    ]


@pytest.mark.asyncio
async def test_short_query_skips_keyword_recall(kfs):
    rec = HybridKeywordRecaller(
        kfs, HybridRetrievalConfig(enabled=True, min_token_query_len=2), KeywordConfig(enabled=True)
    )
    out = await rec.enhance("a", [], ["viking://resources/proj"], _ctx(), limit=5)
    assert out == []


@pytest.mark.asyncio
async def test_cjk_query_is_not_skipped_by_the_token_gate(kfs):
    kfs.upsert(
        ACCOUNT,
        "viking://resources/proj/中文.md",
        "单元圆 认证 接口",
        level=2,
        context_type="resource",
    )
    rec = HybridKeywordRecaller(
        kfs,
        HybridRetrievalConfig(enabled=True, min_token_query_len=2),
        KeywordConfig(enabled=True, cjk_mode="char"),
    )
    # "单元圆" carries no spaces: a whitespace token count would treat it as one
    # token and drop the query, even though the index tokenizes it into three.
    out = await rec.enhance("单元圆", [], ["viking://resources/proj"], _ctx(), limit=10)
    assert any(m.uri.endswith("中文.md") for m in out), out


@pytest.mark.asyncio
async def test_dense_hits_keep_their_dense_score(kfs):
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=True), KeywordConfig(enabled=True))
    out = await rec.enhance("rollback runbook", _dense(), ["viking://resources/proj"], _ctx(), limit=10)
    b = next(m for m in out if m.uri.endswith("B.md"))
    assert b.score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_keyword_only_hit_uses_indexed_metadata(kfs):
    kfs.upsert(
        ACCOUNT,
        "viking://resources/proj/E.md",
        "rollback 2.4.1",
        level=1,
        context_type="memory",
    )
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=True), KeywordConfig(enabled=True))
    out = await rec.enhance("rollback 2.4.1", [], ["viking://resources/proj"], _ctx(), limit=10)
    e = next(m for m in out if m.uri.endswith("E.md"))
    assert e.level == 1
    assert e.context_type == ContextType.MEMORY
