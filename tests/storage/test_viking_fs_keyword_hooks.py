# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingFS enqueues keyword sidecar mutations for copy operations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils.config.keyword_config import KeywordConfig


class _DummyAgfs:
    pass


class _Queue:
    def __init__(self):
        self.messages = []

    async def enqueue(self, msg):
        self.messages.append(msg)
        return "1"


@pytest.mark.asyncio
async def test_copy_enqueues_keyword_copy_messages(monkeypatch, tmp_path):
    from openviking.storage.keywordfs.keyword_fs import KeywordFS

    queue = _Queue()
    monkeypatch.setattr(
        "openviking.storage.queuefs.queue_manager.get_queue_manager",
        lambda: SimpleNamespace(KEYWORD="Keyword", get_queue=lambda name: queue),
    )
    fs = VikingFS(
        agfs=_DummyAgfs(),
        keyword_config=KeywordConfig(enabled=True),
        keyword_fs=KeywordFS(tmp_path, KeywordConfig(enabled=True)),
    )
    await fs._enqueue_keyword_copy(
        ["viking://resources/a.md"],
        "viking://resources",
        "viking://archive",
        SimpleNamespace(account_id="default"),
    )
    assert [(m.kind, m.old_uri, m.new_uri) for m in queue.messages] == [
        ("copy", "viking://resources/a.md", "viking://archive/a.md")
    ]
