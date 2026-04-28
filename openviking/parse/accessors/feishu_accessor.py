# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Feishu/Lark Accessor.

Fetches Feishu/Lark cloud documents using the lark-oapi SDK.
This is the DataAccessor layer extracted from FeishuParser.

Note: This accessor requires the `lark-oapi` package.
Install with: pip install 'openviking[bot-feishu]'
"""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlparse

from openviking_cli.utils.logger import get_logger

from .base import DataAccessor, LocalResource, SourceType

logger = get_logger(__name__)


def _getattr_safe(obj, key: str, default=None):
    """Get attribute from SDK object or dict, with safe fallback."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class FeishuDocument:
    """Result from fetching a Feishu document."""

    doc_type: str  # "docx" only for now
    token: str
    markdown_content: str
    title: str
    meta: Dict[str, Any]


class FeishuAccessor(DataAccessor):
    """
    Accessor for Feishu/Lark cloud documents.

    Supports:
    - Documents: https://*.feishu.cn/docx/{document_id}
    - Wiki pages: https://*.feishu.cn/wiki/{token} (resolves to docx)

    Requires:
    - lark-oapi package
    - FEISHU_APP_ID and FEISHU_APP_SECRET environment variables,
      or configuration in ov.conf
    """

    PRIORITY = 100  # Higher than Git/HTTP, very specific

    # Wiki obj_type normalization (API returns short names)
    _WIKI_TYPE_MAP = {"doc": "docx", "sheet": "sheets", "bitable": "base"}

    # Attributes that skip processing (structural containers or metadata)
    _SKIP_ATTRS = {"page", "table_cell", "quote_container", "grid", "grid_column"}

    # Attribute → special handler method (non-text blocks)
    _SPECIAL_BLOCK_HANDLERS = {
        "divider": "_handle_divider",
        "image": "_handle_image",
        "table": "_table_block_to_markdown",
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
        1: "page",
        2: "text",
        3: "heading1",
        4: "heading2",
        5: "heading3",
        6: "heading4",
        7: "heading5",
        8: "heading6",
        9: "heading7",
        10: "heading8",
        11: "heading9",
        12: "bullet",
        13: "ordered",
        14: "code",
        15: "quote",
        17: "todo",
        19: "callout",
        22: "divider",
        27: "image",
        31: "table",
        32: "table_cell",
        34: "quote_container",
    }

    # All known content attribute names on SDK Block objects (for fallback detection).
    _KNOWN_CONTENT_ATTRS = frozenset(
        {
            "page",
            "text",
            "heading1",
            "heading2",
            "heading3",
            "heading4",
            "heading5",
            "heading6",
            "heading7",
            "heading8",
            "heading9",
            "bullet",
            "ordered",
            "code",
            "quote",
            "todo",
            "callout",
            "divider",
            "image",
            "table",
            "table_cell",
            "quote_container",
            "equation",
            "task",
            "grid",
            "grid_column",
        }
    )

    def __init__(self):
        """Initialize Feishu accessor."""
        self._client = None
        self._config = None

    @property
    def priority(self) -> int:
        return self.PRIORITY

    def can_handle(self, source: Union[str, Path]) -> bool:
        """
        Check if this accessor can handle the source.

        Handles Feishu/Lark cloud document URLs.
        """
        source_str = str(source)

        # Only handle http/https URLs
        if not source_str.startswith(("http://", "https://")):
            return False

        return self._is_feishu_url(source_str)

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Fetch a Feishu document and save to a temporary Markdown file.

        Args:
            source: Feishu document URL
            **kwargs: Additional arguments

        Returns:
            LocalResource pointing to the temporary Markdown file
        """
        source_str = str(source)

        try:
            # Fetch the document and convert to Markdown
            doc = await self._fetch_document(source_str)

            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                prefix="ov_feishu_",
                delete=False,
            )
            temp_file.write(doc.markdown_content)
            temp_file.close()

            # Build metadata
            meta = {
                "feishu_doc_type": doc.doc_type,
                "feishu_token": doc.token,
                "feishu_title": doc.title,
                **doc.meta,
            }

            return LocalResource(
                path=Path(temp_file.name),
                source_type=SourceType.FEISHU,
                original_source=source_str,
                meta=meta,
                is_temporary=True,
            )

        except Exception as e:
            logger.error(f"[FeishuAccessor] Failed to access {source}: {e}", exc_info=True)
            raise

    async def _fetch_document(self, url: str) -> FeishuDocument:
        """
        Fetch a Feishu document and convert to Markdown.

        This method extracts and adapts the logic from FeishuParser.parse().
        """
        import asyncio

        doc_type, token = self._parse_feishu_url(url)
        title = None
        meta = {}

        if doc_type == "wiki":
            # Resolve wiki node to actual document type
            real_type, real_token, title = await asyncio.to_thread(self._resolve_wiki_node, token)
            doc_type, token = real_type, real_token
            meta["wiki_resolved"] = True

        # Only docx is supported
        if doc_type != "docx":
            raise ValueError(
                f"Unsupported Feishu document type: {doc_type}. "
                f"Only docx is supported in this version."
            )

        # Call the handler (in thread pool since lark-oapi is sync)
        markdown, doc_title = await asyncio.to_thread(self._parse_docx, token)

        if title:
            doc_title = title

        meta["original_url"] = url

        return FeishuDocument(
            doc_type=doc_type,
            token=token,
            markdown_content=markdown,
            title=doc_title,
            meta=meta,
        )

    @staticmethod
    def _is_feishu_url(url: str) -> bool:
        """Check if URL is a Feishu/Lark cloud document."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path
        is_feishu_domain = (
            host.endswith(".feishu.cn")
            or host.endswith(".larksuite.com")
            or host.endswith(".larkoffice.com")
        )
        has_doc_path = any(
            path == f"/{t}" or path.startswith(f"/{t}/") for t in ("docx", "wiki", "sheets", "base")
        )
        return is_feishu_domain and has_doc_path

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
        doc_type = path_parts[0]  # docx, wiki
        token = path_parts[1]
        return doc_type, token

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
                lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain).build()
            )
        return self._client

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
                f"Failed to resolve wiki node {token}: code={response.code}, msg={response.msg}"
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

            line = self._block_to_markdown(
                block, block_map, ordered_counter, document_id=document_id
            )
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

    def _block_to_markdown(
        self, block, block_map: Dict, ordered_counter: Dict[str, int], document_id: str = ""
    ) -> Optional[str]:
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

        # Special blocks (non-text: divider, image, table)
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

        from openviking.parse.base import format_table_to_markdown

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
