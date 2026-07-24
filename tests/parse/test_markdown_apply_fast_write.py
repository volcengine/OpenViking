# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MarkdownParser._apply_layout's OPENVIKING_MARKDOWN_APPLY_FAST_WRITE
path: mkdir-dedup + direct viking_fs.write, gated by env (default off) so a
disabled flag reproduces the original per-op replay exactly."""

from unittest.mock import patch

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser, _Layout, _LayoutOp


class FakeVikingFS:
    """Records every mkdir/write call so tests can assert on call counts."""

    def __init__(self):
        self.mkdir_calls = []
        self.files = {}

    async def mkdir(self, uri, exist_ok=False, **kw):
        self.mkdir_calls.append(uri)

    async def write(self, uri, data):
        self.files[uri] = data

    async def write_file(self, uri, content, **kw):
        self.files[uri] = content

    async def glob(self, pattern, uri="", **kw):
        # No images in this layout; let _ingest_local_images short-circuit.
        return {"matches": []}


class TestApplyLayoutFastWrite:
    def _layout(self) -> _Layout:
        # One mkdir op deliberately duplicated (defensive against layouts that
        # emit it more than once for a shared parent), plus a write whose parent
        # dir never got an explicit mkdir op — fast_write must still create every
        # directory that ends up holding a write, exactly once each.
        return _Layout(
            temp_uri="viking://temp/root",
            root_dir="viking://temp/root/doc",
            doc_title="doc",
            doc_name="doc",
            ops=[
                _LayoutOp("mkdir", "viking://temp/root"),
                _LayoutOp("mkdir", "viking://temp/root/doc/sec"),
                _LayoutOp("mkdir", "viking://temp/root/doc/sec"),
                _LayoutOp("write", "viking://temp/root/doc/sec/a.md", "A"),
                _LayoutOp("write", "viking://temp/root/doc/sec/b.md", "B"),
                _LayoutOp("write", "viking://temp/root/doc/other/c.md", "C"),
            ],
        )

    async def test_fast_write_dedupes_mkdir_and_writes_every_section(self, monkeypatch):
        monkeypatch.setenv("OPENVIKING_MARKDOWN_APPLY_FAST_WRITE", "1")
        fake = FakeVikingFS()
        parser = MarkdownParser()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await parser._apply_layout(self._layout())

        # The duplicated mkdir op for "sec" collapses to a single real call...
        assert fake.mkdir_calls.count("viking://temp/root/doc/sec") == 1, fake.mkdir_calls
        # ...and "other", which only ever appears as a write op's parent, still
        # gets created exactly once.
        assert fake.mkdir_calls.count("viking://temp/root/doc/other") == 1, fake.mkdir_calls
        # Every planned write op reaches the fake FS.
        assert fake.files == {
            "viking://temp/root/doc/sec/a.md": "A",
            "viking://temp/root/doc/sec/b.md": "B",
            "viking://temp/root/doc/other/c.md": "C",
        }

    async def test_fast_write_disabled_by_default_keeps_original_replay(self, monkeypatch):
        monkeypatch.delenv("OPENVIKING_MARKDOWN_APPLY_FAST_WRITE", raising=False)
        fake = FakeVikingFS()
        parser = MarkdownParser()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await parser._apply_layout(self._layout())

        # Original path replays ops verbatim: the duplicated mkdir op fires twice.
        assert fake.mkdir_calls.count("viking://temp/root/doc/sec") == 2, fake.mkdir_calls
        assert fake.files == {
            "viking://temp/root/doc/sec/a.md": "A",
            "viking://temp/root/doc/sec/b.md": "B",
            "viking://temp/root/doc/other/c.md": "C",
        }
