#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""summary_only must not fall back to raw file content (#4843)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.utils.embedding_utils import vectorize_file
from openviking_cli.utils.config.embedding_config import TEXT_SOURCE_SUMMARY_ONLY


class _FakeContentType:
    TEXT = "text"
    AUDIO = "audio"
    VIDEO = "video"
    IMAGE = "image"


@pytest.mark.asyncio
async def test_summary_only_skips_when_summary_missing():
    ctx = SimpleNamespace(user="u", account_id="a")
    viking_fs = MagicMock()
    viking_fs.read_file = AsyncMock(return_value="SHOULD_NOT_READ")

    embedding_cfg = SimpleNamespace(
        text_source=TEXT_SOURCE_SUMMARY_ONLY,
        max_input_tokens=4096,
    )

    with (
        patch("openviking.utils.embedding_utils.get_queue_manager") as get_qm,
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=viking_fs),
        patch("openviking.utils.embedding_utils.get_openviking_config") as get_cfg,
        patch(
            "openviking.utils.embedding_utils._resolve_resource_content_type",
            new=AsyncMock(return_value=_FakeContentType.TEXT),
        ),
        patch(
            "openviking.utils.embedding_utils.ResourceContentType",
            _FakeContentType,
        ),
        patch(
            "openviking.utils.embedding_utils._resolve_context_timestamps",
            new=AsyncMock(return_value=(1.0, 1.0)),
        ),
        patch("openviking.utils.embedding_utils.owner_space_for_uri", return_value="space"),
        patch("openviking.utils.embedding_utils.Context") as ContextCls,
    ):
        get_qm.return_value = MagicMock(
            EMBEDDING="embedding",
            get_queue=MagicMock(return_value=MagicMock()),
        )
        get_cfg.return_value = SimpleNamespace(embedding=embedding_cfg)
        ContextCls.return_value = MagicMock()

        enqueued = await vectorize_file(
            "viking://resources/doc.md",
            {"name": "doc.md", "summary": ""},
            parent_uri="viking://resources",
            ctx=ctx,
        )

    assert enqueued is False
    viking_fs.read_file.assert_not_awaited()
