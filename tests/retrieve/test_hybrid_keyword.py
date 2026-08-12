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
async def test_disabled_returns_dense_unmodified(kfs):
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=False), KeywordConfig(enabled=True))
    out = await rec.enhance("rollback", _dense(), ["viking://resources/proj"], _ctx(), limit=10)
    assert [m.uri for m in out] == ["viking://resources/proj/A.md", "viking://resources/proj/B.md"]


@pytest.mark.asyncio
async def test_no_keyword_hit_returns_dense(kfs):
    rec = HybridKeywordRecaller(kfs, HybridRetrievalConfig(enabled=True), KeywordConfig(enabled=True))
    out = await rec.enhance("qqqq zzzz", _dense(), ["viking://resources/proj"], _ctx(), limit=10)
    assert [m.uri for m in out] == ["viking://resources/proj/A.md", "viking://resources/proj/B.md"]
