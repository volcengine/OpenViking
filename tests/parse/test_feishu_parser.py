# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for FeishuParser."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking.parse.parsers.feishu import FeishuParser


def _make_block(**kwargs):
    """Create a mock block object with only the specified attributes populated."""
    # Start with all common attributes as None
    defaults = {
        "block_id": "test_id",
        "block_type": 0,
        "parent_id": "parent_id",
        "children": None,
        "comment_ids": None,
        "add_ons": None,
        "page": None,
        "text": None,
        "heading1": None,
        "heading2": None,
        "heading3": None,
        "heading4": None,
        "heading5": None,
        "heading6": None,
        "heading7": None,
        "heading8": None,
        "heading9": None,
        "bullet": None,
        "ordered": None,
        "code": None,
        "quote": None,
        "todo": None,
        "divider": None,
        "image": None,
        "table": None,
        "table_cell": None,
        "quote_container": None,
        "sheet": None,
        "callout": None,
        "equation": None,
        "task": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_text_content(
    text: str, bold=False, italic=False, inline_code=False, strikethrough=False, link_url=None
):
    """Create a mock text content object with elements."""
    style = SimpleNamespace(
        bold=bold,
        italic=italic,
        inline_code=inline_code,
        strikethrough=strikethrough,
        link=SimpleNamespace(url=link_url) if link_url else None,
    )
    element = SimpleNamespace(
        text_run=SimpleNamespace(content=text, text_element_style=style),
        mention_user=None,
        mention_doc=None,
        equation=None,
    )
    return SimpleNamespace(elements=[element], style=None)


class TestParseFeishuUrl:
    def test_docx_url(self):
        doc_type, token = FeishuParser._parse_feishu_url(
            "https://example.feishu.cn/docx/doxcnABC123"
        )
        assert doc_type == "docx"
        assert token == "doxcnABC123"

    def test_wiki_url(self):
        doc_type, token = FeishuParser._parse_feishu_url("https://example.feishu.cn/wiki/wikiXYZ")
        assert doc_type == "wiki"
        assert token == "wikiXYZ"

    def test_sheets_url(self):
        doc_type, token = FeishuParser._parse_feishu_url(
            "https://example.feishu.cn/sheets/shtcn123"
        )
        assert doc_type == "sheets"

    def test_base_url(self):
        doc_type, token = FeishuParser._parse_feishu_url(
            "https://example.feishu.cn/base/bascn999?table=tbl1"
        )
        assert doc_type == "base"
        assert token == "bascn999"

    def test_larksuite_url(self):
        doc_type, token = FeishuParser._parse_feishu_url(
            "https://example.larksuite.com/docx/abc123"
        )
        assert doc_type == "docx"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            FeishuParser._parse_feishu_url("https://example.feishu.cn/")

    def test_invalid_host_raises(self):
        with pytest.raises(ValueError, match="host not allowed"):
            FeishuParser._parse_feishu_url("https://evil.example/docx/doxcnABC123")


class TestIsFeishuUrl:
    def test_feishu_docx(self):
        from openviking.utils.media_processor import UnifiedResourceProcessor

        assert UnifiedResourceProcessor._is_feishu_url("https://example.feishu.cn/docx/abc123")

    def test_larksuite(self):
        from openviking.utils.media_processor import UnifiedResourceProcessor

        assert UnifiedResourceProcessor._is_feishu_url(
            "https://example.larksuite.com/sheets/abc123"
        )

    def test_non_feishu_url(self):
        from openviking.utils.media_processor import UnifiedResourceProcessor

        assert not UnifiedResourceProcessor._is_feishu_url("https://github.com/foo/bar")

    def test_feishu_non_doc_path(self):
        from openviking.utils.media_processor import UnifiedResourceProcessor

        assert not UnifiedResourceProcessor._is_feishu_url(
            "https://example.feishu.cn/profile/settings"
        )


class TestBlockToMarkdown:
    """Test attribute-driven block-to-markdown conversion."""

    def setup_method(self):
        self.parser = FeishuParser()

    def test_text_block(self):
        block = _make_block(text=_make_text_content("Hello world"))
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "Hello world"

    def test_heading_blocks(self):
        for level in range(1, 7):
            content = _make_text_content(f"Heading {level}")
            block = _make_block(**{f"heading{level}": content})
            result = self.parser._block_to_markdown(block, {}, {})
            assert result == f"{'#' * level} Heading {level}"

    def test_bullet_list(self):
        block = _make_block(bullet=_make_text_content("Item one"))
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "- Item one"

    def test_ordered_list(self):
        counter: dict = {}
        block = _make_block(ordered=_make_text_content("First"))
        result = self.parser._block_to_markdown(block, {}, counter)
        assert result == "1. First"

        block2 = _make_block(ordered=_make_text_content("Second"))
        result2 = self.parser._block_to_markdown(block2, {}, counter)
        assert result2 == "2. Second"

    def test_code_block(self):
        code_content = _make_text_content("print('hello')")
        code_content.style = SimpleNamespace(language="python")
        block = _make_block(code=code_content)
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "```python\nprint('hello')\n```"

    def test_quote_block(self):
        block = _make_block(quote=_make_text_content("A quote"))
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "> A quote"

    def test_todo_block(self):
        todo_content = _make_text_content("Buy milk")
        todo_content.style = SimpleNamespace(done=True)
        block = _make_block(todo=todo_content)
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "- [x] Buy milk"

    def test_divider_block(self):
        block = _make_block(divider=SimpleNamespace())
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "---"

    def test_image_block(self):
        block = _make_block(image=SimpleNamespace(token="img_token_123", alt="screenshot"))
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "![screenshot](feishu://image/img_token_123)"

    def test_skip_page(self):
        block = _make_block(page=SimpleNamespace(elements=[]))
        result = self.parser._block_to_markdown(block, {}, {})
        assert result is None  # page is in _SKIP_ATTRS

    def test_skip_table_cell(self):
        block = _make_block(table_cell=SimpleNamespace())
        result = self.parser._block_to_markdown(block, {}, {})
        assert result is None

    def test_unknown_with_elements_extracts_text(self):
        """Unknown block type with text elements should still extract content."""
        block = _make_block(callout=_make_text_content("Important note"))
        result = self.parser._block_to_markdown(block, {}, {})
        assert result == "Important note"


class TestApplyTextStyle:
    def test_bold(self):
        assert (
            FeishuParser._apply_text_style(
                "text",
                SimpleNamespace(
                    bold=True, italic=False, strikethrough=False, inline_code=False, link=None
                ),
            )
            == "**text**"
        )

    def test_italic(self):
        assert (
            FeishuParser._apply_text_style(
                "text",
                SimpleNamespace(
                    bold=False, italic=True, strikethrough=False, inline_code=False, link=None
                ),
            )
            == "*text*"
        )

    def test_inline_code(self):
        assert (
            FeishuParser._apply_text_style(
                "code",
                SimpleNamespace(
                    bold=False, italic=False, strikethrough=False, inline_code=True, link=None
                ),
            )
            == "`code`"
        )

    def test_link(self):
        result = FeishuParser._apply_text_style(
            "click",
            SimpleNamespace(
                bold=False,
                italic=False,
                strikethrough=False,
                inline_code=False,
                link=SimpleNamespace(url="https://example.com"),
            ),
        )
        assert result == "[click](https://example.com)"

    def test_empty_text(self):
        assert FeishuParser._apply_text_style("", SimpleNamespace(bold=True)) == ""

    def test_none_style(self):
        assert FeishuParser._apply_text_style("text", None) == "text"


class TestFormatBitableField:
    def test_none(self):
        assert FeishuParser._format_bitable_field(None) == ""

    def test_string(self):
        assert FeishuParser._format_bitable_field("hello") == "hello"

    def test_number(self):
        assert FeishuParser._format_bitable_field(42) == "42"

    def test_list_of_dicts(self):
        result = FeishuParser._format_bitable_field([{"text": "A"}, {"name": "B"}])
        assert result == "A, B"

    def test_dict_with_text(self):
        assert FeishuParser._format_bitable_field({"text": "value"}) == "value"


class TestTrimEmptyColumns:
    def test_trim(self):
        rows = [["a", "b", "", ""], ["c", "d", "", ""]]
        result = FeishuParser._trim_empty_columns(rows)
        assert result == [["a", "b"], ["c", "d"]]

    def test_no_trim_needed(self):
        rows = [["a", "b"], ["c", "d"]]
        assert FeishuParser._trim_empty_columns(rows) == rows

    def test_all_empty(self):
        rows = [["", ""], ["", ""]]
        assert FeishuParser._trim_empty_columns(rows) == []


# ========== Mock Integration Tests ==========


def _mock_list_blocks_response(blocks, has_more=False, page_token=None):
    """Create a mock response for docx.v1.document_block.list()."""
    resp = MagicMock()
    resp.success.return_value = True
    resp.data.items = blocks
    resp.data.has_more = has_more
    resp.data.page_token = page_token
    return resp


def _make_sdk_block(block_id, parent_id="doc_id", **attrs):
    """Create a mock SDK block with all attributes defaulting to None."""
    defaults = {
        "block_id": block_id,
        "block_type": 0,
        "parent_id": parent_id,
        "children": None,
        "comment_ids": None,
        "add_ons": None,
        "page": None,
        "text": None,
        "heading1": None,
        "heading2": None,
        "heading3": None,
        "heading4": None,
        "heading5": None,
        "heading6": None,
        "heading7": None,
        "heading8": None,
        "heading9": None,
        "bullet": None,
        "ordered": None,
        "code": None,
        "quote": None,
        "todo": None,
        "divider": None,
        "image": None,
        "table": None,
        "table_cell": None,
        "quote_container": None,
        "sheet": None,
        "callout": None,
        "equation": None,
        "task": None,
    }
    defaults.update(attrs)
    return SimpleNamespace(**defaults)


class TestParseDocxIntegration:
    """Integration tests for _parse_docx with mocked lark-oapi client."""

    def _make_parser_with_mock_client(self, list_response):
        """Create a FeishuParser with a mocked lark-oapi client."""
        parser = FeishuParser()
        mock_client = MagicMock()
        mock_client.docx.v1.document_block.list.return_value = list_response
        parser._client = mock_client
        return parser

    def test_parse_docx_basic(self):
        """Test basic document with page title, heading, and text."""
        blocks = [
            _make_sdk_block(
                "page_id",
                page=SimpleNamespace(
                    elements=[
                        SimpleNamespace(
                            text_run=SimpleNamespace(
                                content="My Document", text_element_style=None
                            ),
                            mention_user=None,
                            mention_doc=None,
                            equation=None,
                        )
                    ]
                ),
            ),
            _make_sdk_block("h1_id", heading2=_make_text_content("Introduction")),
            _make_sdk_block("t1_id", text=_make_text_content("Hello world")),
        ]
        response = _mock_list_blocks_response(blocks)
        parser = self._make_parser_with_mock_client(response)

        markdown, title = parser._parse_docx("test_doc_id")

        assert title == "My Document"
        assert "# My Document" in markdown
        assert "## Introduction" in markdown
        assert "Hello world" in markdown

    def test_parse_docx_with_pagination(self):
        """Test document fetching with multiple pages of blocks."""
        page1_blocks = [
            _make_sdk_block(
                "page_id",
                page=SimpleNamespace(
                    elements=[
                        SimpleNamespace(
                            text_run=SimpleNamespace(
                                content="Paginated Doc", text_element_style=None
                            ),
                            mention_user=None,
                            mention_doc=None,
                            equation=None,
                        )
                    ]
                ),
            ),
            _make_sdk_block("t1_id", text=_make_text_content("Page 1 content")),
        ]
        page2_blocks = [
            _make_sdk_block("t2_id", text=_make_text_content("Page 2 content")),
        ]

        resp1 = _mock_list_blocks_response(page1_blocks, has_more=True, page_token="token2")
        resp2 = _mock_list_blocks_response(page2_blocks, has_more=False)

        parser = FeishuParser()
        mock_client = MagicMock()
        mock_client.docx.v1.document_block.list.side_effect = [resp1, resp2]
        parser._client = mock_client

        markdown, title = parser._parse_docx("test_doc_id")

        assert title == "Paginated Doc"
        assert "Page 1 content" in markdown
        assert "Page 2 content" in markdown
        assert mock_client.docx.v1.document_block.list.call_count == 2

    def test_parse_docx_empty(self):
        """Test empty document."""
        response = _mock_list_blocks_response([])
        parser = self._make_parser_with_mock_client(response)

        markdown, title = parser._parse_docx("empty_doc")

        assert markdown == ""
        assert title == "Untitled"

    def test_parse_docx_api_error(self):
        """Test handling of API error."""
        parser = FeishuParser()
        mock_client = MagicMock()
        error_resp = MagicMock()
        error_resp.success.return_value = False
        error_resp.code = 403
        error_resp.msg = "permission denied"
        mock_client.docx.v1.document_block.list.return_value = error_resp
        parser._client = mock_client

        with pytest.raises(RuntimeError, match="permission denied"):
            parser._parse_docx("forbidden_doc")

    def test_parse_docx_mixed_blocks(self):
        """Test document with various block types."""
        blocks = [
            _make_sdk_block(
                "page_id",
                page=SimpleNamespace(
                    elements=[
                        SimpleNamespace(
                            text_run=SimpleNamespace(content="Report", text_element_style=None),
                            mention_user=None,
                            mention_doc=None,
                            equation=None,
                        )
                    ]
                ),
            ),
            _make_sdk_block("h_id", heading2=_make_text_content("Section 1")),
            _make_sdk_block("b1_id", bullet=_make_text_content("Item A")),
            _make_sdk_block("b2_id", bullet=_make_text_content("Item B")),
            _make_sdk_block("div_id", divider=SimpleNamespace()),
            _make_sdk_block("q_id", quote=_make_text_content("A wise quote")),
            _make_sdk_block(
                "code_id",
                code=SimpleNamespace(
                    elements=[
                        SimpleNamespace(
                            text_run=SimpleNamespace(content="x = 1", text_element_style=None),
                            mention_user=None,
                            mention_doc=None,
                            equation=None,
                        )
                    ],
                    style=SimpleNamespace(language="python"),
                ),
            ),
        ]
        response = _mock_list_blocks_response(blocks)
        parser = self._make_parser_with_mock_client(response)

        markdown, title = parser._parse_docx("test_doc")

        assert title == "Report"
        assert "## Section 1" in markdown
        assert "- Item A" in markdown
        assert "- Item B" in markdown
        assert "---" in markdown
        assert "> A wise quote" in markdown
        assert "```python\nx = 1\n```" in markdown

    def test_parse_docx_ordered_list_reset(self):
        """Test that ordered list counters reset between separate lists."""
        blocks = [
            _make_sdk_block("page_id", page=SimpleNamespace(elements=[])),
            _make_sdk_block("o1", ordered=_make_text_content("First")),
            _make_sdk_block("o2", ordered=_make_text_content("Second")),
            _make_sdk_block(
                "t1", text=_make_text_content("Break")
            ),  # Non-ordered block resets counter
            _make_sdk_block("o3", ordered=_make_text_content("New first")),
        ]
        response = _mock_list_blocks_response(blocks)
        parser = self._make_parser_with_mock_client(response)

        markdown, _ = parser._parse_docx("test_doc")

        assert "1. First" in markdown
        assert "2. Second" in markdown
        assert "1. New first" in markdown


class TestParseAsyncIntegration:
    """Test the async parse() entry point with mocked internals."""

    def test_parse_docx_url(self):
        """Test full parse() flow with a docx URL."""
        parser = FeishuParser()

        blocks = [
            _make_sdk_block(
                "page_id",
                page=SimpleNamespace(
                    elements=[
                        SimpleNamespace(
                            text_run=SimpleNamespace(content="Test Doc", text_element_style=None),
                            mention_user=None,
                            mention_doc=None,
                            equation=None,
                        )
                    ]
                ),
            ),
            _make_sdk_block("t1", text=_make_text_content("Content here")),
        ]
        mock_client = MagicMock()
        mock_client.docx.v1.document_block.list.return_value = _mock_list_blocks_response(blocks)
        parser._client = mock_client

        # Mock MarkdownParser to avoid VikingFS dependency
        mock_md_result = MagicMock()
        mock_md_result.source_format = "markdown"
        mock_md_result.parser_name = "MarkdownParser"
        mock_md_result.parse_time = 0.1
        mock_md_result.meta = {}

        mock_md_instance = MagicMock()

        async def _mock_parse_content(*a, **kw):
            return mock_md_result

        mock_md_instance.parse_content = _mock_parse_content

        with patch.object(parser, "_get_markdown_parser", return_value=mock_md_instance):
            result = asyncio.get_event_loop().run_until_complete(
                parser.parse("https://example.feishu.cn/docx/test123")
            )

        assert result.source_format == "feishu_docx"
        assert result.parser_name == "FeishuParser"
        assert result.meta["feishu_doc_type"] == "docx"
        assert result.meta["feishu_token"] == "test123"

    def test_parse_wiki_url_resolves(self):
        """Test that wiki URLs are resolved to underlying document type."""
        parser = FeishuParser()

        # Mock wiki resolution
        mock_client = MagicMock()
        # Use "doc" (not "docx") to test _WIKI_TYPE_MAP normalization: "doc" -> "docx"
        wiki_node = SimpleNamespace(obj_type="doc", obj_token="real_token", title="Wiki Page")
        wiki_resp = MagicMock()
        wiki_resp.success.return_value = True
        wiki_resp.data.node = wiki_node
        mock_client.wiki.v2.space.get_node.return_value = wiki_resp

        # Mock docx blocks
        blocks = [
            _make_sdk_block("page_id", page=SimpleNamespace(elements=[])),
            _make_sdk_block("t1", text=_make_text_content("Wiki content")),
        ]
        mock_client.docx.v1.document_block.list.return_value = _mock_list_blocks_response(blocks)
        parser._client = mock_client

        mock_md_result = MagicMock()
        mock_md_result.source_format = "markdown"
        mock_md_result.parser_name = "MarkdownParser"
        mock_md_result.parse_time = 0.1
        mock_md_result.meta = {}

        mock_md_instance = MagicMock()

        async def _mock_parse_content(*a, **kw):
            return mock_md_result

        mock_md_instance.parse_content = _mock_parse_content

        with patch.object(parser, "_get_markdown_parser", return_value=mock_md_instance):
            result = asyncio.get_event_loop().run_until_complete(
                parser.parse("https://example.feishu.cn/wiki/wiki_token")
            )

        assert result.source_format == "feishu_docx"
        assert result.meta["feishu_doc_type"] == "docx"
        assert result.meta["feishu_token"] == "real_token"

    def test_parse_unsupported_type(self):
        """Test that unsupported document types return error ParseResult."""
        parser = FeishuParser()
        parser._client = MagicMock()  # Won't be called

        result = asyncio.get_event_loop().run_until_complete(
            parser.parse("https://example.feishu.cn/mindnote/abc123")
        )

        assert result.warnings
        assert "Unsupported" in result.warnings[0]

    def test_parse_rejects_invalid_host(self):
        """Main parse() entry point must reject non-Feishu hosts."""
        parser = FeishuParser()

        result = asyncio.get_event_loop().run_until_complete(
            parser.parse("https://evil.example/docx/doxcn123")
        )

        assert result.source_format == "feishu"
        assert result.warnings
        assert "host not allowed" in result.warnings[0]
        assert "feishu_doc_type" not in result.meta
        assert "feishu_token" not in result.meta
