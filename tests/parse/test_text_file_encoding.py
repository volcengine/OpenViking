import pytest

from openviking.parse.parsers import upload_utils
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.upload_utils import detect_and_convert_encoding


def test_markdown_file_read_normalizes_gb18030_text(tmp_path):
    content = "# 大奉打更人校对版\n\n这是一段中文正文，用于验证旧编码文本不会被解析成乱码。\n"
    path = tmp_path / "legacy-chinese.md"
    path.write_bytes(content.encode("gb18030"))

    assert MarkdownParser()._read_file(path) == content


def test_markdown_file_read_normalizes_gb18030_four_byte_text(tmp_path):
    content = "# 标题 😀 emoji\n\n正文内容\n"
    path = tmp_path / "legacy-chinese-emoji.md"
    path.write_bytes(content.encode("gb18030"))

    assert MarkdownParser()._read_file(path) == content


def test_markdown_file_read_strips_utf8_bom(tmp_path):
    content = "# Heading\n\nBody\n"
    path = tmp_path / "utf8-bom.md"
    path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    assert MarkdownParser()._read_file(path) == content


@pytest.mark.parametrize(
    ("encoding", "content"),
    [
        ("shift_jis", "# タイトル\n本文です\n"),
        ("big5", "# 標題\n繁體中文內容\n"),
        ("euc-kr", "# 제목\n본문 내용\n"),
        ("cp1252", "Price: €10 – café\n"),
        ("latin-1", "café naïve\n"),
    ],
)
def test_markdown_file_read_normalizes_representative_legacy_encodings(tmp_path, encoding, content):
    path = tmp_path / f"legacy-{encoding}.md"
    path.write_bytes(content.encode(encoding))

    assert MarkdownParser()._read_file(path) == content


def test_text_file_read_preserves_legacy_latin1_fallback_for_unrecognized_bytes(tmp_path):
    raw = bytes([1, 2, 3, 4, 5, 0xFF, 0xFE, 0xFD])
    path = tmp_path / "control-heavy.txt"
    path.write_bytes(raw)

    assert MarkdownParser()._read_file(path) == raw.decode("latin-1")


def test_encoding_detection_handles_empty_text_without_warning(tmp_path, monkeypatch):
    path = tmp_path / "empty.md"
    warnings = []

    monkeypatch.setattr(upload_utils.logger, "warning", lambda message: warnings.append(message))

    assert detect_and_convert_encoding(b"", path) == b""
    assert warnings == []
