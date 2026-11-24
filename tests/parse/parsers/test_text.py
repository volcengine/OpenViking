from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
from openviking.parse.registry import ParserRegistry


async def test_file_parse_preserves_text_parser_metadata_and_content(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Plain text content", encoding="utf-8")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    result = await ParserRegistry().parse(source, parse_output_store=store)

    assert result.source_format == "text"
    assert result.parser_name == "TextParser"
    assert result.artifact_ref is not None
    assert set(await read_artifact_manifest(store, result.artifact_ref)) == {"notes/notes.md"}
    assert await store.read_bytes(result.artifact_ref, "notes/notes.md") == b"Plain text content"
