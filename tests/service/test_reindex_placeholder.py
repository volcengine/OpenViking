# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Directory marker reindexing preserves authored summaries and filters placeholders."""

import pytest

from openviking.core.context import ContextLevel
from openviking.core.namespace import context_type_for_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import ReindexExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "marker", "uri"),
    [
        pytest.param(
            ContextLevel.ABSTRACT, "abstract", "viking://resources/reindex-notes", id="abstract"
        ),
        pytest.param(
            ContextLevel.OVERVIEW,
            "overview",
            "viking://user/default/memories/reindex-notes",
            id="overview",
        ),
    ],
)
async def test_reindex_directory_marker_removes_placeholder_and_preserves_authored_content(
    service, monkeypatch, level, marker, uri
):
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: service)
    fs = service.viking_fs
    db = service.vikingdb_manager
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    await fs.mkdir(uri, ctx=ctx)
    backend = await db.get_account_backend(ctx.account_id)
    await db.upsert(
        {
            "uri": uri,
            "level": int(level),
            "context_type": context_type_for_uri(uri),
            "abstract": "stale summary",
            "vector": [0.1] * backend.vector_dim,
        },
        ctx=ctx,
    )
    enqueue = []

    async def capture_embedding(message):
        enqueue.append(message)
        return True

    monkeypatch.setattr(db, "enqueue_embedding_msg", capture_embedding)
    reindex = ReindexExecutor()

    assert await db.get_context_by_uri(uri=uri, level=int(level), ctx=ctx)
    await reindex.reindex_directory_marker(dir_uri=uri, level=level, ctx=ctx)
    assert await db.get_context_by_uri(uri=uri, level=int(level), ctx=ctx) == []
    assert enqueue == []

    summary = f"# Project notes\n\nCurrent CLI output: [Directory {marker} is not ready]"
    await fs.write_file(f"{uri}/.{marker}.md", summary, ctx=ctx)
    await reindex.reindex_directory_marker(dir_uri=uri, level=level, ctx=ctx)
    assert [(message.context_data["uri"], message.message) for message in enqueue] == [
        (uri, summary)
    ]
