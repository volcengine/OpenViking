# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MarkdownParser layout application.

``_apply_layout`` replays ``layout.ops`` verbatim: every mkdir op runs, and each
section is written through ``_write_section`` so it keeps VikingFS's parent-dir
and encrypted-write handling. Concurrent parses must keep link-rewrite inputs
isolated while replaying their layouts.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from openviking.parse.image_rewrite import IMAGE_MAPPINGS_FILENAME, _rewrite_content
from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
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


@pytest.mark.parametrize(
    "image_refs",
    [
        (),
        ("assets/chart.png", "./assets/chart.png", "assets/../assets/chart.png"),
        ("assets/chart.png", "other/chart.png", "./assets/chart.png"),
    ],
)
async def test_markdown_parser_writes_local_artifact_and_manifest(tmp_path: Path, image_refs):
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    parser = MarkdownParser()
    content = "# Hello\n\nworld"
    for index, reference in enumerate(image_refs):
        image = tmp_path / reference
        image.parent.mkdir(parents=True, exist_ok=True)
        if not image.exists():
            Image.new("RGB", (64, 64), "red" if index == 0 else "blue").save(image)
        content += (
            f'\n\n<img src="{reference}">' if index == 2 else f"\n\n![image {index}]({reference})"
        )

    source = tmp_path / "hello.md"
    source.write_text(content, encoding="utf-8")
    result = await parser.parse(source, parse_output_store=store)

    assert result.artifact_ref is not None
    assert result.artifact_ref.backend == "local"
    assert result.artifact_ref.resource_rel == "hello"
    root = Path(result.artifact_ref.root, "hello")
    assert (root / "hello.md").read_text() == content
    manifest = await read_artifact_manifest(store, result.artifact_ref)
    expected_paths = {"hello/hello.md"}
    if image_refs:
        mappings = json.loads((root / IMAGE_MAPPINGS_FILENAME).read_text())
        mapping = mappings["hello.md"]
        assert set(mapping) == set(image_refs)
        sources = {(tmp_path / reference).resolve() for reference in image_refs}
        assert len(set(mapping.values())) == len(sources)
        assert len(list(root.glob("*.png"))) == len(sources)
        for reference, name in mapping.items():
            assert (root / name).read_bytes() == (tmp_path / reference).read_bytes()
        rewritten, count = _rewrite_content(
            content, "viking://resources/hello", set(mapping.values()), mapping
        )
        assert count == len(image_refs)
        assert rewritten.count("viking://resources/hello/") == len(image_refs)
        expected_paths.update(f"hello/{name}" for name in mapping.values())
        expected_paths.add(f"hello/{IMAGE_MAPPINGS_FILENAME}")
    assert set(manifest) == expected_paths
