# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


class _FakeVikingFS:
    def __init__(self):
        self.entries = {
            "viking://user/default/memories/preferences": [
                {"name": "a.txt", "isDir": False},
            ],
        }
        self.files = {
            "viking://user/default/memories/preferences/.overview.md": "# preferences\n\n## Detailed Description\n### Session Context Management\nCached summary",
            "viking://user/default/memories/preferences/.summary_cache.json": json.dumps(
                {"a.txt": "Cached summary"}
            ),
            "viking://user/default/memories/preferences/a.txt": "hello",
        }
        self.writes = []

    async def ls(self, uri, ctx=None):
        return self.entries.get(uri, [])

    async def read_file(self, path, ctx=None):
        return self.files.get(path, "")

    async def write_file(self, path, content, ctx=None):
        self.files[path] = content
        self.writes.append((path, content))

    async def stat(self, path, ctx=None):
        content = self.files.get(path, "")
        return {"size": len(content)}


@pytest.mark.asyncio
async def test_process_memory_directory_reuses_summary_cache(monkeypatch):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    processor = SemanticProcessor(max_concurrent_llm=1)
    generate_summary = AsyncMock()
    generate_overview = AsyncMock(return_value="# preferences\n\nCached overview")
    vectorize_directory = AsyncMock()
    monkeypatch.setattr(processor, "_generate_single_file_summary", generate_summary)
    monkeypatch.setattr(processor, "_generate_overview", generate_overview)
    monkeypatch.setattr(processor, "_vectorize_directory", vectorize_directory)
    monkeypatch.setattr(
        processor, "_enforce_size_limits", lambda overview, abstract: (overview, abstract)
    )

    msg = SemanticMsg(
        uri="viking://user/default/memories/preferences",
        context_type="memory",
        recursive=False,
        changes={"added": [], "modified": [], "deleted": []},
    )

    await processor._process_memory_directory(msg)

    generate_summary.assert_not_called()
    generate_overview.assert_awaited_once()
    file_summaries = generate_overview.await_args.args[1]
    assert file_summaries == [{"name": "a.txt", "summary": "Cached summary"}]
    assert any(path.endswith("/.summary_cache.json") for path, _ in fake_fs.writes)
