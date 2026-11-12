# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for git-restore directory-marker reindex embedding text."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.service import reindex_executor as reindex_module
from openviking.service.reindex_executor import ReindexExecutor
from openviking.storage.abstract_overview import embedding_text_for_body
from openviking_cli.session.user_id import UserIdentifier


class _MarkerFS:
    """FS surface reindex_directory_marker touches, with fixed marker bodies."""

    def __init__(self, abstract: str, overview: str):
        self._abstract = abstract
        self._overview = overview
        self._async_agfs = SimpleNamespace(
            pathlock_acquire_exact=AsyncMock(return_value=object()),
            pathlock_release=AsyncMock(),
        )

    def _uri_to_path(self, uri, ctx=None):
        return f"/local{uri}"

    async def abstract(self, uri, ctx=None):
        return self._abstract

    async def overview(self, uri, ctx=None):
        return self._overview


async def _reindex_marker(monkeypatch, level, abstract, overview):
    dir_uri = "viking://user/acc1/resources/project"
    fs = _MarkerFS(abstract, overview)
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    executor = ReindexExecutor()
    upsert = AsyncMock()
    monkeypatch.setattr(executor, "_upsert_context", upsert)
    ctx = RequestContext(user=UserIdentifier("acc1", "u1"), role=Role("root"))

    await executor.reindex_directory_marker(dir_uri=dir_uri, level=level, ctx=ctx)

    assert upsert.await_args is not None
    return dir_uri, upsert.await_args.kwargs


async def test_abstract_marker_uses_canonical_embedding_text(monkeypatch):
    body = "Project summary covering the resource tree."
    dir_uri, kwargs = await _reindex_marker(
        monkeypatch, ContextLevel.ABSTRACT, abstract=body, overview=""
    )

    # The rebuild path (and every other L0/L1 writer) embeds the canonical
    # OKF form built by embedding_text_for_body, i.e. the visible body plus
    # the whitelisted ``directory`` metadata — not the bare body. On main the
    # marker path embedded only the body, so a git restore of .abstract.md
    # silently changed the vector text for an identical summary.
    assert kwargs["vector_text"] == embedding_text_for_body(
        ContextLevel.ABSTRACT, dir_uri, body
    )
    assert "directory" in kwargs["vector_text"]
    assert kwargs["abstract"] == body


async def test_overview_marker_uses_canonical_embedding_text(monkeypatch):
    abstract = "Short abstract."
    overview = "Overview listing each file with its purpose."
    dir_uri, kwargs = await _reindex_marker(
        monkeypatch, ContextLevel.OVERVIEW, abstract=abstract, overview=overview
    )

    assert kwargs["vector_text"] == embedding_text_for_body(
        ContextLevel.OVERVIEW, dir_uri, overview
    )
    # L1 keeps the existing contract: the abstract scalar carries the overview.
    assert kwargs["abstract"] == overview
