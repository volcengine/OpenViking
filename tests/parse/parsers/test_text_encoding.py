"""Encoding normalisation for uploaded legacy text files.

``text_encoding`` is the only thing standing between a Windows-authored GBK
document and a memory store full of mojibake. It decides the encoding from a
combination of charset detection, BOM sniffing and script heuristics, so every
test here pins one of those decisions to a concrete byte sequence rather than a
mock. Several samples are deliberately short: that is where the heuristics have
the least evidence to work with, and where they disagree with each other.
"""

import codecs

import pytest

from openviking.parse.parsers.text_encoding import normalize_text_bytes

CHINESE = "这是一个用于测试的中文段落，包含足够多的汉字以触发检测。"
TRADITIONAL = "這是一段繁體中文內容，用於測試編碼偵測是否正確。"
JAPANESE = "これは日本語のテキストです。文字コードの検出を確認します。"
KOREAN = "이것은 한국어 텍스트입니다. 인코딩 감지를 확인합니다。"


def _normalized(text: str, encoding: str) -> str:
    return normalize_text_bytes(text.encode(encoding), f"sample.{encoding}").decode("utf-8")


def test_utf8_input_is_returned_byte_for_byte():
    raw = CHINESE.encode("utf-8")

    assert normalize_text_bytes(raw, "notes.md") == raw


def test_ascii_input_is_returned_byte_for_byte():
    raw = b"plain ascii\nsecond line\n"

    assert normalize_text_bytes(raw, "notes.txt") == raw


def test_empty_input_is_returned_unchanged():
    assert normalize_text_bytes(b"", "empty.txt") == b""


@pytest.mark.parametrize(
    ("bom", "encoding"),
    [
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
    ],
)
def test_byte_order_mark_is_consumed_and_text_preserved(bom, encoding):
    result = normalize_text_bytes(bom + CHINESE.encode(encoding), "bom.txt").decode("utf-8")

    assert result == CHINESE
    assert not result.startswith("\ufeff")


@pytest.mark.parametrize(
    ("text", "encoding"),
    [
        (CHINESE, "gbk"),
        (CHINESE, "gb18030"),
        (TRADITIONAL, "big5"),
        (JAPANESE, "shift_jis"),
        (KOREAN, "euc_kr"),
        ("Grüße aus München – die Straße ist gesperrt.", "cp1252"),
    ],
)
def test_legacy_encodings_are_converted_to_utf8(text, encoding):
    assert _normalized(text, encoding) == text


@pytest.mark.parametrize(
    ("text", "encoding"),
    [
        ("数据库", "gbk"),
        ("配置中心", "gbk"),
        (TRADITIONAL[:3], "big5"),
        (TRADITIONAL[:6], "big5"),
        ("これはテストです", "shift_jis"),
        ("日本語文書", "shift_jis"),
    ],
)
def test_short_samples_are_not_misdetected(text, encoding):
    """A few characters leave every codec plausible, so the heuristics decide."""
    assert _normalized(text, encoding) == text


def test_short_big5_sample_does_not_decode_into_private_use_glyphs():
    """GBK can decode the Big5 bytes of ``繁體字`` without raising.

    Two of the three characters land inside the Unicode Private Use Area, which
    real text never contains, yet the decoded string is otherwise "valid": no
    replacement characters, no control characters. Before the private-use check
    this candidate was accepted, and the three-character title came back as
    ``羉砰`` plus one unassigned glyph.
    """
    result = _normalized(TRADITIONAL[:3], "big5")

    assert result == TRADITIONAL[:3]
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in result)


def test_binary_content_is_left_untouched():
    raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64

    assert normalize_text_bytes(raw, "picture.txt") == raw


def test_result_is_stable_under_repeated_normalisation():
    once = normalize_text_bytes(CHINESE.encode("gbk"), "repeat.txt")

    assert normalize_text_bytes(once, "repeat.txt") == once
