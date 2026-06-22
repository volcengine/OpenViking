from openviking.parse.parsers.markdown import MarkdownParser


def test_markdown_file_read_normalizes_gb18030_text(tmp_path):
    content = "# 大奉打更人校对版\n\n这是一段中文正文，用于验证旧编码文本不会被解析成乱码。\n"
    path = tmp_path / "legacy-chinese.md"
    path.write_bytes(content.encode("gb18030"))

    assert MarkdownParser()._read_file(path) == content
