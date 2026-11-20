# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for the AnyDoc parser's blocking converter."""

import asyncio
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.anydoc import AnyDocParser
from openviking.parse.parsers.anydoc_converter import AnyDocConverter
from openviking_cli.utils.config.parser_config import AnydocConfig


async def test_anydoc_parser_offloads_docx_conversion(monkeypatch, tmp_path: Path):
    parser = AnyDocParser(anydoc_config=AnydocConfig(max_table_rows=7))
    source = tmp_path / "sample.docx"
    source.write_bytes(b"document fixture")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    started = asyncio.Event()
    release = Event()

    def convert(self, path, *, resource_name, storage, max_table_rows):
        loop.call_soon_threadsafe(started.set)
        assert get_ident() != loop_thread
        assert path == source
        assert resource_name == "sample"
        assert max_table_rows == 7
        assert release.wait(5), "converter never released"
        return SimpleNamespace(
            markdown="# Converted document\n\nBody from the Word file.",
            source_format="docx",
            images_saved=0,
        )

    monkeypatch.setattr(AnyDocConverter, "convert", convert)
    task = asyncio.create_task(parser.parse(source, parse_output_store=store))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not task.done()
    finally:
        release.set()
        result = await asyncio.wait_for(task, timeout=5)

    assert result.source_format == "docx"
    assert result.parser_name == "AnyDocParser"
    assert result.artifact_ref is not None
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    assert (root / "sample.md").read_text() == "# Converted document\n\nBody from the Word file."
