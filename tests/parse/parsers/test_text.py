from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers.text import TextParser


async def test_file_parse_preserves_text_parser_metadata_and_instruction(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Plain text content", encoding="utf-8")
    delegate_parse = AsyncMock(
        return_value=create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_format="markdown",
            parser_name="MarkdownParser",
        )
    )
    parser = TextParser.__new__(TextParser)
    parser._md_parser = SimpleNamespace(parse=delegate_parse)

    result = await parser.parse(source, instruction="preserve this", marker=True)

    delegate_parse.assert_awaited_once_with(source, instruction="preserve this", marker=True)
    assert result.source_format == "text"
    assert result.parser_name == "TextParser"
