# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for chunk_meta tagging on _LayoutOp during markdown layout planning.

These exercise the parser-side bookkeeping that the /api/v1/resources/full
endpoint depends on, without requiring the AGFS native binding (which the
server-tier tests need).
"""

from __future__ import annotations

import asyncio

from openviking.parse.parsers.markdown import MarkdownParser, _LayoutOp
from openviking_cli.utils.config.parser_config import ParserConfig


def _make_parser(max_chars: int = 200, max_size: int = 50) -> MarkdownParser:
    cfg = ParserConfig(max_section_size=max_size, max_section_chars=max_chars)
    return MarkdownParser(config=cfg)


def _split_writes(ops: list[_LayoutOp]) -> list[_LayoutOp]:
    return [o for o in ops if o.kind == "write" and o.chunk_meta is not None]


def test_split_content_tags_chunk_meta_with_correct_total():
    parser = _make_parser()
    ops: list[_LayoutOp] = []
    long_text = "para " * 500
    asyncio.run(parser._split_content(ops, "viking://temp/sec", "doc", long_text, max_size=20))

    writes = _split_writes(ops)
    assert writes, "expected at least one chunk write op"
    total = writes[0].chunk_meta[1]
    assert total == len(writes)
    # chunk_index must be 0..N-1, in order
    assert [w.chunk_meta[0] for w in writes] == list(range(total))
    # Chunk total is consistent across chunks
    assert {w.chunk_meta[1] for w in writes} == {total}


def test_save_merged_split_path_tags_chunk_meta():
    parser = _make_parser()
    ops: list[_LayoutOp] = []
    # Build a section large enough to exceed both the char and token limit so
    # _save_merged hits its split branch.
    long_section = "paragraph one\n\n" + ("hello world " * 200)
    sections = [("title", long_section, 0)]
    asyncio.run(parser._save_merged(ops, "viking://temp/parent", sections))

    writes = _split_writes(ops)
    assert writes, "merged split path must tag chunk_meta"
    total = writes[0].chunk_meta[1]
    assert [w.chunk_meta[0] for w in writes] == list(range(total))


def test_layout_op_carries_chunk_meta_field():
    op = _LayoutOp("write", "viking://x/y_1.md", "abc", chunk_meta=(0, 3))
    assert op.chunk_meta == (0, 3)
    op2 = _LayoutOp("write", "viking://x/y.md", "abc")
    assert op2.chunk_meta is None
