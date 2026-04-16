"""Tests for parser config propagation through registries and composed parsers."""

from openviking.parse.parsers.feishu import FeishuParser
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.parser_config import (
    FeishuConfig,
    HTMLConfig,
    MarkdownConfig,
    PDFConfig,
    TextConfig,
)


def test_parser_registry_propagates_parser_configs_to_registered_parsers():
    markdown_config = MarkdownConfig(max_section_size=4321, max_section_chars=9876)
    text_config = TextConfig(max_section_size=3456, max_section_chars=8765)
    pdf_config = PDFConfig(
        strategy="local",
        max_section_size=2345,
        max_section_chars=7654,
    )
    html_config = HTMLConfig(max_section_size=1234, max_section_chars=6543)

    registry = ParserRegistry(
        parser_configs={
            "markdown": markdown_config,
            "text": text_config,
            "pdf": pdf_config,
            "html": html_config,
        }
    )

    markdown_parser = registry.get_parser("markdown")
    assert markdown_parser is not None
    assert markdown_parser.config.max_section_size == 4321
    assert markdown_parser.config.max_section_chars == 9876

    text_parser = registry.get_parser("text")
    assert text_parser is not None
    assert text_parser._md_parser.config.max_section_size == 3456
    assert text_parser._md_parser.config.max_section_chars == 8765

    pdf_parser = registry.get_parser("pdf")
    assert pdf_parser is not None
    assert pdf_parser.config.max_section_size == 2345
    assert pdf_parser.config.max_section_chars == 7654
    assert pdf_parser._get_markdown_parser().config.max_section_size == 2345
    assert pdf_parser._get_markdown_parser().config.max_section_chars == 7654

    html_parser = registry.get_parser("html")
    assert html_parser is not None
    assert html_parser.config.max_section_size == 1234
    assert html_parser.config.max_section_chars == 6543
    assert html_parser._get_markdown_parser().config.max_section_size == 1234
    assert html_parser._get_markdown_parser().config.max_section_chars == 6543


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
