# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared Feishu block-to-Markdown conversion primitives.

Fetching and authentication remain owned by the caller.  The accessor and
legacy parser provide their own block policy tables so this mixin can share
the conversion algorithm without changing either surface's supported blocks.
"""

from typing import Any, Dict, Optional

from openviking.parse.base import format_table_to_markdown


def getattr_safe(obj: Any, key: str, default=None):
    """Get an attribute from an SDK object or mapping."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class FeishuBlockMarkdownMixin:
    """Convert Feishu SDK blocks using caller-provided policy tables."""

    _BLOCK_TYPE_TO_ATTR: Dict[int, str]
    _KNOWN_CONTENT_ATTRS: frozenset[str]
    _SKIP_ATTRS: set[str]
    _SPECIAL_BLOCK_HANDLERS: Dict[str, str]
    _TEXT_FORMAT: Dict[str, str]

    def _detect_block_attr(self, block) -> Optional[str]:
        """Detect the populated content attribute on an SDK block."""
        block_type = getattr(block, "block_type", None)
        if block_type is not None:
            attr = self._BLOCK_TYPE_TO_ATTR.get(block_type)
            if attr:
                return attr

        for attr in self._KNOWN_CONTENT_ATTRS:
            if getattr(block, attr, None) is not None:
                return attr
        return None

    def _block_to_markdown(
        self,
        block,
        block_map: Dict,
        ordered_counter: Dict[str, int],
        document_id: str = "",
    ) -> Optional[str]:
        """Convert one SDK block according to the caller's block policy."""
        attr = self._detect_block_attr(block)
        if attr is None or attr in self._SKIP_ATTRS:
            return None

        if attr != "ordered":
            parent_id = block.parent_id or ""
            ordered_counter.pop(parent_id, None)

        special_handler = self._SPECIAL_BLOCK_HANDLERS.get(attr)
        if special_handler:
            return getattr(self, special_handler)(
                block,
                block_map,
                document_id=document_id,
            )

        content_obj = getattr(block, attr, None)
        if not content_obj or not getattr(content_obj, "elements", None):
            return None

        text = self._extract_text_from_elements(content_obj.elements)
        if not text:
            return None

        if attr.startswith("heading"):
            level = int(attr.replace("heading", "") or "1")
            return f"{'#' * level} {text}"

        if attr == "ordered":
            parent_id = block.parent_id or ""
            counter = ordered_counter.get(parent_id, 0) + 1
            ordered_counter[parent_id] = counter
            return f"{counter}. {text}"

        if attr == "code":
            style = getattr(content_obj, "style", None)
            lang = str(getattr(style, "language", "") or "") if style else ""
            return f"```{lang}\n{text}\n```"

        if attr == "todo":
            style = getattr(content_obj, "style", None)
            checkbox = "[x]" if style and getattr(style, "done", False) else "[ ]"
            return f"- {checkbox} {text}"

        fmt = self._TEXT_FORMAT.get(attr)
        if fmt:
            return fmt.format(text=text)
        return text

    @staticmethod
    def _handle_divider(block, block_map: Dict = None, **_) -> str:
        """Convert a divider block to Markdown."""
        return "---"

    @staticmethod
    def _handle_image(block, block_map: Dict = None, **_) -> Optional[str]:
        """Convert an image block to a deferred Feishu media reference."""
        image = block.image
        if not image:
            return None
        file_token = image.token or ""
        alt_text = getattr(image, "alt", "") or "image"
        return f"![{alt_text}](feishu://image/{file_token})"

    def _extract_block_text(self, block, attr_name: str) -> str:
        """Extract formatted text from one named block attribute."""
        content_obj = getattr(block, attr_name, None)
        if content_obj and getattr(content_obj, "elements", None):
            return self._extract_text_from_elements(content_obj.elements)
        return ""

    def _extract_text_from_elements(self, elements) -> str:
        """Convert Feishu TextElement SDK objects to formatted text."""
        if not elements:
            return ""
        parts = []
        for element in elements:
            text_run = element.text_run
            if text_run:
                content = self._apply_text_style(
                    text_run.content or "",
                    text_run.text_element_style,
                )
                parts.append(content)
                continue

            mention_user = element.mention_user
            if mention_user:
                parts.append(f"@{getattr_safe(mention_user, 'user_id', 'user')}")
                continue

            mention_doc = element.mention_doc
            if mention_doc:
                title = getattr_safe(mention_doc, "title", "document")
                url = getattr_safe(mention_doc, "url", "")
                parts.append(f"[{title}]({url})" if url else str(title))
                continue

            equation = element.equation
            if equation:
                parts.append(f"${getattr_safe(equation, 'content', '')}$")

        return "".join(parts)

    @staticmethod
    def _apply_text_style(text: str, style) -> str:
        """Apply Markdown formatting from a TextElementStyle object."""
        if not text or not style:
            return text
        if getattr(style, "inline_code", False):
            return f"`{text}`"
        link = getattr(style, "link", None)
        if link:
            url = getattr_safe(link, "url", "")
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
        """Convert a table block and its cell children to Markdown."""
        table = block.table
        children = block.children
        if not table or not children or not table.property:
            return None

        row_size = table.property.row_size or 0
        col_size = table.property.column_size or 0
        if not row_size or not col_size:
            return None

        rows = []
        for row_idx in range(row_size):
            row = []
            for col_idx in range(col_size):
                cell_idx = row_idx * col_size + col_idx
                if cell_idx < len(children):
                    cell_block = block_map.get(children[cell_idx])
                    row.append(self._extract_cell_text(cell_block, block_map))
                else:
                    row.append("")
            rows.append(row)

        return format_table_to_markdown(rows, has_header=True) if rows else None

    def _extract_cell_text(self, cell_block, block_map: Dict) -> str:
        """Extract text from a table cell's child blocks."""
        if not cell_block or not cell_block.children:
            return ""
        texts = []
        for child_id in cell_block.children:
            child = block_map.get(child_id)
            if not child:
                continue
            attr = self._detect_block_attr(child)
            if attr:
                text = self._extract_block_text(child, attr)
                if text:
                    texts.append(text)
        return " ".join(texts)
