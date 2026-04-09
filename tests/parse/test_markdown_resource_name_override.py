from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_markdown_parser_uses_resource_name_for_output_naming():
    from openviking.parse.parsers.markdown import MarkdownParser

    fs = MagicMock()
    fs.mkdir = AsyncMock()
    fs.write_file = AsyncMock()

    parser = MarkdownParser()
    with patch.object(parser, "_get_viking_fs", return_value=fs):
        result = await parser.parse_content(
            "# Title\n\nHello\n",
            source_path="/tmp/upload_deadbeef.md",
            resource_name="aa",
        )

    assert result is not None
    written_paths = [call.args[0] for call in fs.write_file.call_args_list]
    assert any(p.endswith("/aa.md") for p in written_paths)
