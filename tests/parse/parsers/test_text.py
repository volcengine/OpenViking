from pathlib import Path

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.text import TextParser


async def test_file_parse_preserves_text_content_and_format(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Plain text content", encoding="utf-8")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    result = await TextParser().parse(source, parse_output_store=store)

    assert result.source_format == "text"
    assert result.parser_name == "TextParser"
    assert result.artifact_ref is not None
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    assert (root / "notes.md").read_text() == "Plain text content"
