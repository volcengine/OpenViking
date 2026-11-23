# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.parser_config import ParserConfig


async def test_repeated_headings_with_different_section_sizes_preserve_all_groups(tmp_path):
    source = tmp_path / "notes.md"
    sections = [f"# Repeated heading\n\nSection {i} " + ("内容" * (22 + i)) for i in range(8)]
    source.write_text("\n\n".join(sections), encoding="utf-8")
    store = LocalParseOutputStore(str(tmp_path / "out"))
    registry = ParserRegistry({"markdown": ParserConfig(max_section_chars=180)})

    result = await registry.parse(source, parse_output_store=store)

    assert result.artifact_ref is not None
    files = list(Path(result.artifact_ref.root).rglob("*.md"))
    assert len(files) == 4
    assert all("Repeated_heading" in file.name for file in files)
    bodies = [file.read_text(encoding="utf-8") for file in files]
    assert all(sum(section in body for body in bodies) == 1 for section in sections)


@pytest.mark.parametrize(
    ("source_name", "output"),
    [
        ("notes.txt", "notes/notes.md"),
        ("main.py", "main.py/main.md"),
        ("style.css", "style/style.md"),
    ],
)
async def test_staged_source_keeps_the_name_used_by_the_importer(tmp_path, source_name, output):
    original = "source content"
    staged = tmp_path / "temporary-download"
    staged.write_text(original, encoding="utf-8")
    store = LocalParseOutputStore(str(tmp_path / "out"))

    result = await ParserRegistry().parse(staged, source_name=source_name, parse_output_store=store)

    assert result.artifact_ref is not None
    assert await store.read_bytes(result.artifact_ref, output) == original.encode()
