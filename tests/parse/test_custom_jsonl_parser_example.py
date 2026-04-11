# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import importlib.util
from pathlib import Path

import pytest

from openviking.parse.base import NodeType, ParseResult, ResourceNode


def _load_custom_jsonl_parser_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "custom-parser-plugin"
        / "custom_jsonl_parser.py"
    )
    spec = importlib.util.spec_from_file_location("examples.custom_jsonl_parser", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_custom_jsonl_parser_converts_title_content_records_to_markdown(tmp_path):
    module = _load_custom_jsonl_parser_module()
    parser = module.MyCustomJsonlParser()

    source = tmp_path / "records.jsonl"
    source.write_text(
        '{"title": "test title1", "content": "test content1"}\n'
        '{"title": "test title2", "content": "test content2"}\n',
        encoding="utf-8",
    )

    captured = {}

    async def fake_parse_content(content, source_path=None, instruction="", **kwargs):
        captured["content"] = content
        captured["source_path"] = source_path
        captured["instruction"] = instruction
        return ParseResult(root=ResourceNode(type=NodeType.ROOT, title="records"))

    parser._md_parser.parse_content = fake_parse_content

    result = await parser.parse(source, instruction="keep-order")

    assert captured["content"] == (
        "# test title1\n\ntest content1\n\n# test title2\n\ntest content2"
    )
    assert captured["source_path"] == str(source)
    assert captured["instruction"] == "keep-order"
    assert result.source_format == "jsonl"
    assert result.parser_name == "MyCustomJsonlParser"
