# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Feishu/Lark cloud document parser for OpenViking.

Supports:
- Documents: https://*.feishu.cn/docx/{document_id}
- Wiki pages: https://*.feishu.cn/wiki/{token}
- Spreadsheets: https://*.feishu.cn/sheets/{token}
- Bitable: https://*.feishu.cn/base/{app_token}

All types are converted to Markdown then parsed via MarkdownParser.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
    format_table_to_markdown,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _getattr_safe(obj, key: str, default=None):
    """Get attribute from SDK object or dict, with safe fallback."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class FeishuParser(BaseParser):
    """Parser for Feishu/Lark cloud documents.

    Block type detection uses a two-tier strategy:
    1. Primary: block_type integer → attribute name lookup via _BLOCK_TYPE_TO_ATTR
       (reliable, not affected by SDK changes)
    2. Fallback: scan _KNOWN_CONTENT_ATTRS whitelist for unknown block types
       (auto-compat for new types without code changes)
    """

    # Attributes that skip processing (structural containers or metadata)
    _SKIP_ATTRS = {"page", "table_cell", "quote_container", "grid", "grid_column"}

    # Attribute → special handler method (non-text blocks)
    _SPECIAL_BLOCK_HANDLERS = {
        "divider": "_handle_divider",
        "image": "_handle_image",
        "table": "_table_block_to_markdown",
        "sheet": "_embedded_sheet_to_markdown",
    }

    # Attribute → markdown prefix template for text-bearing blocks.
    # "{text}" is replaced with extracted text content.
    # Headings are handled dynamically (heading1-heading9 → # through #########).
    _TEXT_FORMAT = {
        "bullet": "- {text}",
        "quote": "> {text}",
    }

    # Known block_type integer → SDK attribute name mapping.
    # Primary dispatch mechanism for reliable block detection.
    # Source: Feishu OpenAPI documentation + lark-oapi SDK Block class.
    _BLOCK_TYPE_TO_ATTR = {
        1: "page", 2: "text",
        3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4",
        7: "heading5", 8: "heading6", 9: "heading7", 10: "heading8", 11: "heading9",
        12: "bullet", 13: "ordered", 14: "code", 15: "quote",
        17: "todo", 18: "bitable", 19: "callout",
        22: "divider", 24: "file",
        27: "image",
        30: "sheet",
        31: "table", 32: "table_cell",
        34: "quote_container",
    }

    # All known content attribute names on SDK Block objects (for fallback detection).
    _KNOWN_CONTENT_ATTRS = frozenset({
        "page", "text", "heading1", "heading2", "heading3", "heading4",
        "heading5", "heading6", "heading7", "heading8", "heading9",
        "bullet", "ordered", "code", "quote", "todo", "callout",
        "divider", "image", "table", "table_cell", "quote_container",
        "sheet", "file", "bitable", "equation", "task",
        "grid", "grid_column", "iframe", "board", "chat_card", "diagram",
        "agenda", "agenda_item", "agenda_item_content", "agenda_item_title",
        "ai_template", "isv", "jira_issue", "link_preview", "meeting_notes_qa",
        "mindnote", "okr", "okr_key_result", "okr_objective", "okr_progress",
        "project", "reference_base", "reference_synced", "source_synced",
        "sub_page_list", "undefined", "view", "wiki_catalog",
    })

    # Document type → parse method name mapping.
    # Wiki nodes are resolved to one of these types via _resolve_wiki_node.
    # New types can be supported by adding an entry here and the corresponding method.
    _DOC_TYPE_HANDLERS = {
        "docx": "_parse_docx",
        "sheets": "_parse_sheets",
        "base": "_parse_bitable",
    }

    # Wiki obj_type normalization (API returns short names)
    _WIKI_TYPE_MAP = {"doc": "docx", "sheet": "sheets", "bitable": "base"}

    def __init__(self):
        self._client = None
        self._config = None

    @property
    def supported_extensions(self) -> List[str]:
        return []  # URL-based parser, no file extensions

    # ========== Configuration & Client ==========

    def _get_config(self):
        """Get FeishuConfig from OpenViking config."""
        if self._config is None:
            from openviking_cli.utils.config import get_openviking_config

            self._config = get_openviking_config().feishu
        return self._config

    def _get_client(self):
        """Lazy-init lark-oapi client."""
        if self._client is None:
            try:
                import lark_oapi as lark
            except ImportError:
                raise ImportError(
                    "lark-oapi is required for Feishu document parsing. "
                    "Install it with: pip install 'openviking[bot-feishu]'"
                )
            config = self._get_config()
            app_id = config.app_id or os.getenv("FEISHU_APP_ID", "")
            app_secret = config.app_secret or os.getenv("FEISHU_APP_SECRET", "")
            if not app_id or not app_secret:
                raise ValueError(
                    "Feishu credentials not configured. Set FEISHU_APP_ID and "
                    "FEISHU_APP_SECRET environment variables, or configure in ov.conf."
                )
            domain = config.domain or "https://open.feishu.cn"
            self._client = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .domain(domain)
                .build()
            )
        return self._client

    # ========== URL Parsing ==========

    @staticmethod
    def _parse_feishu_url(url: str) -> Tuple[str, str]:
        """
        Extract doc_type and token from Feishu URL.

        Returns:
            (doc_type, token) e.g. ("docx", "doxcnABC123")
        """
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            raise ValueError(f"Cannot parse Feishu URL: {url}")
        doc_type = path_parts[0]  # docx, wiki, sheets, base
        token = path_parts[1]
        return doc_type, token

    # ========== Main Parse ==========

    async def parse(
        self, source: Union[str, Path], instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse a Feishu cloud document URL."""
        url = str(source)
        start_time = time.time()

        try:
            doc_type, token = self._parse_feishu_url(url)

            title = None
            if doc_type == "wiki":
                real_type, real_token, title = await asyncio.to_thread(
                    self._resolve_wiki_node, token
                )
                doc_type, token = real_type, real_token

            handler_name = self._DOC_TYPE_HANDLERS.get(doc_type)
            if not handler_name:
                raise ValueError(
                    f"Unsupported Feishu document type: {doc_type}. "
                    f"Supported: {list(self._DOC_TYPE_HANDLERS.keys())}"
                )
            markdown, doc_title = await asyncio.to_thread(
                getattr(self, handler_name), token
            )

            if title:
                doc_title = title

            # Delegate to MarkdownParser
            from openviking.parse.parsers.markdown import MarkdownParser

            md_parser = MarkdownParser()
            result = await md_parser.parse_content(
                markdown, source_path=url, instruction=instruction, **kwargs
            )
            result.source_format = f"feishu_{doc_type}"
            result.parser_name = "FeishuParser"
            result.parse_time = time.time() - start_time
            result.meta["feishu_doc_type"] = doc_type
            result.meta["feishu_token"] = token
            return result

        except Exception as e:
            logger.error(f"[FeishuParser] Failed to parse {url}: {e}")
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=url,
                source_format="feishu",
                parser_name="FeishuParser",
                parse_time=time.time() - start_time,
                warnings=[f"Feishu parse failed: {e}"],
            )

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """Not typically used for Feishu (URL-based parser)."""
        if source_path and ("feishu.cn" in source_path or "larksuite.com" in source_path):
            return await self.parse(source_path, instruction=instruction, **kwargs)
        raise NotImplementedError("FeishuParser requires a Feishu URL. Use parse() instead.")

    # ========== Wiki Resolution ==========

    def _resolve_wiki_node(self, token: str) -> Tuple[str, str, Optional[str]]:
        """
        Resolve wiki token to actual document type, token, and title.

        Returns:
            (doc_type, obj_token, title)
        """
        from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

        client = self._get_client()
        request = GetNodeSpaceRequest.builder().token(token).build()
        response = client.wiki.v2.space.get_node(request)
        if not response.success():
            raise RuntimeError(
                f"Failed to resolve wiki node {token}: "
                f"code={response.code}, msg={response.msg}"
            )
        node = response.data.node
        obj_type = node.obj_type or ""
        obj_token = node.obj_token or ""
        title = node.title

        # Normalize type names
        doc_type = self._WIKI_TYPE_MAP.get(obj_type, obj_type)

        return doc_type, obj_token, title

    # ========== Docx Parsing ==========

    def _parse_docx(self, document_id: str) -> Tuple[str, str]:
        """
        Fetch all blocks and convert to Markdown.

        Returns:
            (markdown_content, document_title)
        """
        blocks = self._fetch_all_blocks(document_id)
        if not blocks:
            return "", "Untitled"

        # Build block lookup by block_id
        block_map = {b.block_id: b for b in blocks}

        # Find title from page block
        doc_title = "Untitled"
        for b in blocks:
            if b.page is not None:
                if b.page.elements:
                    doc_title = self._extract_text_from_elements(b.page.elements)
                break

        # Convert blocks to markdown
        markdown_lines = []
        ordered_counter: Dict[str, int] = {}

        for block in blocks:
            if block.page is not None:
                continue  # Skip page container

            line = self._block_to_markdown(block, block_map, ordered_counter, document_id=document_id)
            if line is not None:
                markdown_lines.append(line)

        markdown = "\n\n".join(markdown_lines)

        if doc_title and doc_title != "Untitled":
            markdown = f"# {doc_title}\n\n{markdown}"

        return markdown, doc_title

    def _fetch_all_blocks(self, document_id: str) -> list:
        """Fetch all blocks with pagination. Returns list of SDK block objects."""
        from lark_oapi.api.docx.v1 import ListDocumentBlockRequest

        client = self._get_client()
        all_blocks = []
        page_token = None

        while True:
            builder = (
                ListDocumentBlockRequest.builder()
                .document_id(document_id)
                .page_size(500)
                .document_revision_id(-1)
            )
            if page_token:
                builder = builder.page_token(page_token)

            request = builder.build()
            response = client.docx.v1.document_block.list(request)

            if not response.success():
                raise RuntimeError(
                    f"Failed to fetch blocks for {document_id}: "
                    f"code={response.code}, msg={response.msg}"
                )

            items = response.data.items or []
            all_blocks.extend(items)

            if not response.data.has_more:
                break
            page_token = response.data.page_token

        return all_blocks

    # ========== Block -> Markdown Conversion ==========

    def _detect_block_attr(self, block) -> Optional[str]:
        """Detect which content attribute is populated on a block object.

        Uses block_type integer as the primary dispatch (reliable), falling
        back to attribute inspection over a known whitelist for unknown types.
        """
        # Primary: lookup by block_type integer
        block_type = getattr(block, "block_type", None)
        if block_type is not None:
            attr = self._BLOCK_TYPE_TO_ATTR.get(block_type)
            if attr:
                return attr

        # Fallback: scan known content attributes for unknown block types
        for attr in self._KNOWN_CONTENT_ATTRS:
            if getattr(block, attr, None) is not None:
                return attr
        return None

    def _block_to_markdown(self, block, block_map: Dict, ordered_counter: Dict[str, int],
                           document_id: str = "") -> Optional[str]:
        """Convert a single SDK block object to markdown string.

        Uses block_type integer for primary dispatch, with attribute whitelist
        fallback for unknown types. Formatting is data-driven via _TEXT_FORMAT
        and _SPECIAL_BLOCK_HANDLERS tables.
        """
        attr = self._detect_block_attr(block)

        if attr is None:
            return None

        # Skip structural containers (processed via their children)
        if attr in self._SKIP_ATTRS:
            return None

        # Reset ordered list counter when any non-ordered block appears
        if attr != "ordered":
            parent_id = block.parent_id or ""
            if parent_id in ordered_counter:
                del ordered_counter[parent_id]

        # Special blocks (non-text: divider, image, table, sheet)
        special_handler = self._SPECIAL_BLOCK_HANDLERS.get(attr)
        if special_handler:
            return getattr(self, special_handler)(block, block_map, document_id=document_id)

        # --- Text-bearing blocks: extract elements, apply formatting ---
        content_obj = getattr(block, attr, None)
        if not content_obj or not hasattr(content_obj, "elements") or not content_obj.elements:
            return None

        text = self._extract_text_from_elements(content_obj.elements)
        if not text:
            return None

        # Headings: heading1 -> #, heading2 -> ##, ...
        if attr.startswith("heading"):
            level = int(attr.replace("heading", "") or "1")
            return f"{'#' * level} {text}"

        # Ordered list (needs counter state)
        if attr == "ordered":
            parent_id = block.parent_id or ""
            counter = ordered_counter.get(parent_id, 0) + 1
            ordered_counter[parent_id] = counter
            return f"{counter}. {text}"

        # Code block (needs language from style)
        if attr == "code":
            lang = ""
            if hasattr(content_obj, "style") and content_obj.style:
                lang = str(getattr(content_obj.style, "language", "") or "")
            return f"```{lang}\n{text}\n```"

        # Todo (needs done state from style)
        if attr == "todo":
            done = False
            if hasattr(content_obj, "style") and content_obj.style:
                done = getattr(content_obj.style, "done", False)
            checkbox = "[x]" if done else "[ ]"
            return f"- {checkbox} {text}"

        # Simple template formatting (bullet, quote, etc.)
        fmt = self._TEXT_FORMAT.get(attr)
        if fmt:
            return fmt.format(text=text)

        # Default: return plain text (covers callout, equation, task, unknown, etc.)
        return text

    @staticmethod
    def _handle_divider(block, block_map: Dict = None, **_) -> str:
        """Convert divider block to markdown."""
        return "---"

    @staticmethod
    def _handle_image(block, block_map: Dict = None, **_) -> Optional[str]:
        """Convert image block to markdown."""
        image = block.image
        if not image:
            return None
        file_token = image.token or ""
        alt_text = getattr(image, "alt", "") or "image"
        return f"![{alt_text}](feishu://image/{file_token})"

    def _extract_block_text(self, block, attr_name: str) -> str:
        """Extract text from a block's named attribute (e.g. block.text, block.heading2)."""
        content_obj = getattr(block, attr_name, None)
        if content_obj and hasattr(content_obj, "elements") and content_obj.elements:
            return self._extract_text_from_elements(content_obj.elements)
        return ""

    def _extract_text_from_elements(self, elements) -> str:
        """Convert Feishu TextElement SDK objects to formatted text."""
        if not elements:
            return ""
        parts = []
        for element in elements:
            # TextRun
            text_run = element.text_run
            if text_run:
                content = text_run.content or ""
                style = text_run.text_element_style
                content = self._apply_text_style(content, style)
                parts.append(content)
                continue

            # MentionUser
            mention_user = element.mention_user
            if mention_user:
                user_id = _getattr_safe(mention_user, "user_id", "user")
                parts.append(f"@{user_id}")
                continue

            # MentionDoc
            mention_doc = element.mention_doc
            if mention_doc:
                title = _getattr_safe(mention_doc, "title", "document")
                url = _getattr_safe(mention_doc, "url", "")
                parts.append(f"[{title}]({url})" if url else str(title))
                continue

            # Equation
            equation = element.equation
            if equation:
                parts.append(f"${_getattr_safe(equation, 'content', '')}$")
                continue

        return "".join(parts)

    @staticmethod
    def _apply_text_style(text: str, style) -> str:
        """Apply markdown formatting based on TextElementStyle SDK object."""
        if not text or not style:
            return text
        # inline_code (SDK uses 'inline_code', not 'code_inline')
        if getattr(style, "inline_code", False):
            return f"`{text}`"
        # link
        link = getattr(style, "link", None)
        if link:
            url = _getattr_safe(link, "url", "")
            if url:
                text = f"[{text}]({url})"
        if getattr(style, "bold", False):
            text = f"**{text}**"
        if getattr(style, "italic", False):
            text = f"*{text}*"
        if getattr(style, "strikethrough", False):
            text = f"~~{text}~~"
        return text

    def _table_block_to_markdown(self, block, block_map: Dict, **_) -> Optional[str]:
        """Convert table block to markdown table."""
        table = block.table
        children = block.children
        if not table or not children:
            return None

        prop = table.property
        if not prop:
            return None
        row_size = prop.row_size or 0
        col_size = prop.column_size or 0
        if not row_size or not col_size:
            return None

        rows = []
        for row_idx in range(row_size):
            row = []
            for col_idx in range(col_size):
                cell_idx = row_idx * col_size + col_idx
                if cell_idx < len(children):
                    cell_block_id = children[cell_idx]
                    cell_block = block_map.get(cell_block_id)
                    cell_text = self._extract_cell_text(cell_block, block_map)
                    row.append(cell_text)
                else:
                    row.append("")
            rows.append(row)

        return format_table_to_markdown(rows, has_header=True) if rows else None

    def _extract_cell_text(self, cell_block, block_map: Dict) -> str:
        """Extract text from a table cell block by reading its children."""
        if not cell_block or not cell_block.children:
            return ""
        texts = []
        for child_id in cell_block.children:
            child = block_map.get(child_id)
            if not child:
                continue
            # Use attribute-driven detection to find text in any block type
            attr = self._detect_block_attr(child)
            if attr:
                text = self._extract_block_text(child, attr)
                if text:
                    texts.append(text)
        return " ".join(texts)

    # ========== Embedded Sheet in Docx ==========

    def _embedded_sheet_to_markdown(self, block, block_map: Dict = None, *,
                                     document_id: str = "", **_) -> Optional[str]:
        """Convert an embedded sheet block to markdown table.

        These blocks appear in docx documents when a user embeds a spreadsheet
        view. The block contains a sheet token in the format
        ``{spreadsheet_token}_{sheet_id}``.
        """
        import lark_oapi as lark

        client = self._get_client()
        block_id = block.block_id
        doc_id = document_id or block.parent_id

        raw_req = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(f"/open-apis/docx/v1/documents/{doc_id}/blocks/{block_id}")
            .token_types({lark.AccessTokenType.TENANT})
            .build()
        )
        raw_resp = client.request(raw_req)
        if not raw_resp.success():
            return None

        data = json.loads(raw_resp.raw.content)
        sheet_token = (
            data.get("data", {}).get("block", {}).get("sheet", {}).get("token", "")
        )
        if not sheet_token:
            return None

        # Parse token: {spreadsheet_token}_{sheet_id}
        parts = sheet_token.rsplit("_", 1)
        if len(parts) != 2:
            return None
        spreadsheet_token, sheet_id = parts

        # Read cell data and trim empty trailing columns
        try:
            rows = self._read_sheet_range(
                spreadsheet_token, sheet_id, max_rows=100, max_cols=26
            )
            if rows:
                rows = self._trim_empty_columns(rows)
            if rows:
                return format_table_to_markdown(rows, has_header=True)
        except Exception as e:
            logger.warning(f"[FeishuParser] Failed to read embedded sheet {sheet_token}: {e}")

        return None

    @staticmethod
    def _trim_empty_columns(rows: List[List[str]]) -> List[List[str]]:
        """Remove trailing columns that are entirely empty across all rows."""
        if not rows:
            return rows
        max_cols = max(len(r) for r in rows)
        # Find rightmost non-empty column
        last_col = 0
        for col in range(max_cols):
            for row in rows:
                if col < len(row) and row[col].strip():
                    last_col = col + 1
        if last_col == 0:
            return []
        return [row[:last_col] for row in rows]

    # ========== Sheets Parsing ==========

    def _parse_sheets(self, token: str) -> Tuple[str, str]:
        """Fetch spreadsheet data and convert to Markdown."""
        from lark_oapi.api.sheets.v3 import (
            GetSpreadsheetRequest,
            QuerySpreadsheetSheetRequest,
        )

        client = self._get_client()
        config = self._get_config()

        # Get spreadsheet metadata
        meta_request = GetSpreadsheetRequest.builder().spreadsheet_token(token).build()
        meta_response = client.sheets.v3.spreadsheet.get(meta_request)
        title = "Spreadsheet"
        if meta_response.success() and meta_response.data.spreadsheet:
            title = meta_response.data.spreadsheet.title or title

        # Get sheet list
        sheets_request = (
            QuerySpreadsheetSheetRequest.builder().spreadsheet_token(token).build()
        )
        sheets_response = client.sheets.v3.spreadsheet_sheet.query(sheets_request)
        if not sheets_response.success():
            raise RuntimeError(
                f"Failed to fetch sheets for {token}: "
                f"code={sheets_response.code}, msg={sheets_response.msg}"
            )

        sheets = sheets_response.data.sheets or []
        markdown_parts = [f"# {title}", f"**Sheets:** {len(sheets)}"]

        for sheet in sheets:
            sheet_id = sheet.sheet_id
            sheet_title = sheet.title or sheet_id
            row_count = sheet.grid_properties.row_count if sheet.grid_properties else 0
            col_count = sheet.grid_properties.column_count if sheet.grid_properties else 0

            parts = [f"## Sheet: {sheet_title}"]

            if row_count == 0 or col_count == 0:
                parts.append("*Empty sheet*")
                markdown_parts.append("\n\n".join(parts))
                continue

            parts.append(f"**Dimensions:** {row_count} rows x {col_count} columns")

            rows_to_read = min(row_count, config.max_rows_per_sheet)
            cell_data = self._read_sheet_range(token, sheet_id, rows_to_read, col_count)

            if cell_data:
                table_md = format_table_to_markdown(cell_data, has_header=True)
                parts.append(table_md)

            if row_count > config.max_rows_per_sheet:
                parts.append(
                    f"\n*... {row_count - config.max_rows_per_sheet} more rows truncated ...*"
                )

            markdown_parts.append("\n\n".join(parts))

        return "\n\n".join(markdown_parts), title

    def _read_sheet_range(
        self, token: str, sheet_id: str, max_rows: int, max_cols: int
    ) -> List[List[str]]:
        """Read cell values from a sheet range using lark-oapi SDK."""
        import lark_oapi as lark

        client = self._get_client()
        end_col = self._col_number_to_letter(min(max_cols, 26))
        range_str = f"{sheet_id}!A1:{end_col}{max_rows}"

        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(f"/open-apis/sheets/v2/spreadsheets/{token}/values/{range_str}")
            .token_types({lark.AccessTokenType.TENANT})
            .build()
        )

        response = client.request(request)
        if not response.success():
            raise RuntimeError(
                f"Failed to read sheet range: code={response.code}, msg={response.msg}"
            )

        data = json.loads(response.raw.content)
        values = data.get("data", {}).get("valueRange", {}).get("values", [])
        return [
            [str(cell) if cell is not None else "" for cell in row] for row in values
        ]

    @staticmethod
    def _col_number_to_letter(n: int) -> str:
        """Convert column number (1-based) to letter (A, B, ..., Z)."""
        return chr(ord("A") + n - 1) if 1 <= n <= 26 else "Z"

    # ========== Bitable Parsing ==========

    def _parse_bitable(self, app_token: str) -> Tuple[str, str]:
        """Fetch bitable data and convert to Markdown."""
        from lark_oapi.api.bitable.v1 import (
            ListAppTableFieldRequest,
            ListAppTableRecordRequest,
            ListAppTableRequest,
        )

        client = self._get_client()
        config = self._get_config()

        tables_request = ListAppTableRequest.builder().app_token(app_token).build()
        tables_response = client.bitable.v1.app_table.list(tables_request)
        if not tables_response.success():
            raise RuntimeError(
                f"Failed to list bitable tables: "
                f"code={tables_response.code}, msg={tables_response.msg}"
            )

        tables = tables_response.data.items or []
        title = f"Bitable ({len(tables)} tables)"
        markdown_parts = [f"# {title}"]

        for table in tables:
            table_id = table.table_id
            table_name = table.name or table_id

            fields_request = (
                ListAppTableFieldRequest.builder()
                .app_token(app_token)
                .table_id(table_id)
                .build()
            )
            fields_response = client.bitable.v1.app_table_field.list(fields_request)
            field_names: List[str] = []
            if fields_response.success() and fields_response.data.items:
                field_names = [f.field_name for f in fields_response.data.items]

            all_records: list = []
            page_token = None
            while len(all_records) < config.max_records_per_table:
                remaining = config.max_records_per_table - len(all_records)
                page_size = min(remaining, 500)
                builder = (
                    ListAppTableRecordRequest.builder()
                    .app_token(app_token)
                    .table_id(table_id)
                    .page_size(page_size)
                )
                if page_token:
                    builder = builder.page_token(page_token)
                records_response = client.bitable.v1.app_table_record.list(
                    builder.build()
                )
                if not records_response.success():
                    break
                items = records_response.data.items or []
                all_records.extend(items)
                if not records_response.data.has_more:
                    break
                page_token = records_response.data.page_token

            parts = [f"## {table_name}"]
            parts.append(f"**Records:** {len(all_records)}")

            if field_names and all_records:
                rows = [field_names]
                for record in all_records:
                    fields = record.fields or {}
                    row = [self._format_bitable_field(fields.get(fn, "")) for fn in field_names]
                    rows.append(row)
                parts.append(format_table_to_markdown(rows, has_header=True))

            if len(all_records) >= config.max_records_per_table:
                parts.append(f"\n*... records truncated at {config.max_records_per_table} ...*")

            markdown_parts.append("\n\n".join(parts))

        return "\n\n".join(markdown_parts), title

    @staticmethod
    def _format_bitable_field(value: Any) -> str:
        """Format bitable field value to string."""
        if value is None:
            return ""
        if isinstance(value, list):
            texts = []
            for item in value:
                if isinstance(item, dict):
                    texts.append(item.get("text", item.get("name", str(item))))
                else:
                    texts.append(str(item))
            return ", ".join(texts)
        if isinstance(value, dict):
            return value.get("text", value.get("name", str(value)))
        return str(value)
