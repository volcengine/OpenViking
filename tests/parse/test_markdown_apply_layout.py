# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MarkdownParser layout application.

``_apply_layout`` replays ``layout.ops`` verbatim: every mkdir op runs, and each
section is written through ``_write_section`` so it keeps VikingFS's parent-dir
and encrypted-write handling. Concurrent parses must keep link-rewrite inputs
isolated while replaying their layouts.
"""

import asyncio
from unittest.mock import patch

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser, _Layout, _LayoutOp


class FakeVikingFS:
    """Records every mkdir/write call so tests can assert on call counts."""

    def __init__(self):
        self.mkdir_calls = []
        self.files = {}
        self.raw_write_calls = []
        self._temp_counter = 0

    async def mkdir(self, uri, exist_ok=False, **kw):
        self.mkdir_calls.append(uri)

    async def write(self, uri, data):
        # Bypasses parent-dir and encrypted-write handling; recorded so a
        # regression back to this path is visible.
        self.raw_write_calls.append(uri)
        self.files[uri] = data

    async def write_file(self, uri, content, **kw):
        self.files[uri] = content

    async def glob(self, pattern, uri="", **kw):
        # No images in this layout; let _ingest_local_images short-circuit.
        return {"matches": []}

    def create_temp_uri(self):
        self._temp_counter += 1
        return f"viking://temp/concurrent_{self._temp_counter}"


class InterleavingFakeVikingFS(FakeVikingFS):
    """Pause the first parse until a second parse enters layout application."""

    def __init__(self):
        super().__init__()
        self._initial_mkdir_count = 0
        self._both_parses_started = asyncio.Event()

    async def mkdir(self, uri, exist_ok=False, **kw):
        await super().mkdir(uri, exist_ok=exist_ok, **kw)
        if self._initial_mkdir_count < 2:
            self._initial_mkdir_count += 1
            if self._initial_mkdir_count == 2:
                self._both_parses_started.set()
            await self._both_parses_started.wait()


class TestApplyLayout:
    def _layout(self) -> _Layout:
        # One mkdir op is deliberately duplicated to show ops are replayed
        # verbatim rather than deduplicated.
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

    async def test_replays_every_op_and_writes_each_section(self):
        fake = FakeVikingFS()
        parser = MarkdownParser()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await parser._apply_layout(self._layout())

        assert fake.mkdir_calls.count("viking://temp/root/doc/sec") == 2, fake.mkdir_calls
        assert fake.files == {
            "viking://temp/root/doc/sec/a.md": "A",
            "viking://temp/root/doc/sec/b.md": "B",
            "viking://temp/root/doc/other/c.md": "C",
        }
        assert fake.raw_write_calls == []

    async def test_concurrent_parses_keep_link_rewrite_inputs_isolated(self, tmp_path):
        fake = InterleavingFakeVikingFS()
        parser = MarkdownParser()

        roots = {}
        for name in ("a", "b"):
            root = tmp_path / name
            root.mkdir()
            (root / f"{name}.md").write_text("source", encoding="utf-8")
            (root / f"{name}.txt").write_text("target", encoding="utf-8")
            roots[name] = root

        async def parse(name):
            root = roots[name]
            return await parser.parse_content(
                f"[target](./{name}.txt)",
                source_path=str(root / f"{name}.md"),
                base_dir=root,
                enable_link_rewrite=True,
                link_rewrite_root=str(root),
            )

        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            results = await asyncio.gather(parse("a"), parse("b"))

        for name, result in zip(("a", "b"), results, strict=True):
            uri = f"{result.temp_dir_path}/{name}/{name}.md"
            assert fake.files[uri] == f"[target](../{name}.txt)"
