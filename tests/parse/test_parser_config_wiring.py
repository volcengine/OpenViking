"""Tests for parser config propagation through registries and composed parsers."""

from openviking.parse.parsers.feishu import FeishuParser
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import (
    FeishuConfig,
    HTMLConfig,
    PDFConfig,
)


def test_pdf_parser_passes_its_config_to_nested_markdown_parser():
    parser = PDFParser(PDFConfig(strategy="local", max_section_size=2222, max_section_chars=5555))

    markdown_parser = parser._get_markdown_parser()

    assert markdown_parser.config.max_section_size == 2222
    assert markdown_parser.config.max_section_chars == 5555


def test_html_parser_passes_its_config_to_nested_markdown_parser():
    parser = HTMLParser(config=HTMLConfig(max_section_size=2111, max_section_chars=5444))

    markdown_parser = parser._get_markdown_parser()

    assert markdown_parser.config.max_section_size == 2111
    assert markdown_parser.config.max_section_chars == 5444


def test_feishu_parser_passes_its_config_to_nested_markdown_parser():
    parser = FeishuParser(config=FeishuConfig(max_section_size=1999, max_section_chars=5333))

    markdown_parser = parser._get_markdown_parser()

    assert markdown_parser.config.max_section_size == 1999
    assert markdown_parser.config.max_section_chars == 5333
