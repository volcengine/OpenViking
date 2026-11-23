# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Direct indexing must use the real parent for single-file targets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils import embedding_utils
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize("is_dir", [False, True])
async def test_index_resource_file_uses_actual_parent(monkeypatch, is_dir):
    uri = "viking://resources/docs/a.md"
    fs = SimpleNamespace(
        _get_vector_store=lambda: None,
        stat=AsyncMock(return_value={"isDir": is_dir}),
        exists=AsyncMock(return_value=False),
        ls=AsyncMock(side_effect=NotADirectoryError(uri))
        if not is_dir
        else AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    vectorize = AsyncMock(return_value=True)
    monkeypatch.setattr(embedding_utils, "vectorize_file", vectorize)
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    await embedding_utils.index_resource(uri, ctx)
    if is_dir:
        vectorize.assert_not_awaited()
        fs.ls.assert_awaited_once()
    else:
        vectorize.assert_awaited_once()
        assert vectorize.await_args.kwargs["file_path"] == uri
        assert vectorize.await_args.kwargs["parent_uri"] == "viking://resources/docs"
        fs.ls.assert_not_awaited()
