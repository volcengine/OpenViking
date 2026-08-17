# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unified AnyDoc parser for Office documents and EPUB files."""

import asyncio
import html
import re
import time
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

from openviking.parse.accessors.mime_types import get_preferred_extension
from openviking.parse.base import ParseResult
from openviking.parse.image_validation import is_valid_image
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _RenderedDocument:
    markdown: str
    detected_format: str
    warnings: list[str]
    assets_referenced: int
    images_extracted: int


@dataclass(frozen=True)
class _ResolvedAnchor:
    fragment: str
    emit_html: bool


@dataclass
class _TextRun:
    text: str
    style: Any


class _AnyDocMarkdownRenderer:
    """Render AnyDoc's public document model while materializing image assets."""

    def __init__(self, document: Any, *, source_format: str, resource_name: str, storage: Any):
        self.document = document
        self.source_format = source_format
        self.resource_name = resource_name
        self.storage = storage
        self.assets = {asset.id: asset for asset in document.assets}
        self.asset_paths: dict[int, Optional[str]] = {}
        self.warnings: list[str] = []
        self.assets_referenced: set[int] = set()
        self.images_extracted = 0
        self.note_numbers = self._number_notes()
        self.anchors = self._resolve_anchors()

    def render(self) -> str:
        parts = [part for block in self.document.blocks if (part := self._render_block(block))]

        rendered_note_numbers: set[int] = set()
        ordered_notes = sorted(
            (
                (self.note_numbers[note.id], note)
                for note in self.document.notes
                if note.id in self.note_numbers
            ),
            key=lambda item: item[0],
        )
        for number, note in ordered_notes:
            if number in rendered_note_numbers:
                continue
            body = self._render_blocks(note.blocks)
            if not body:
                continue
            rendered_note_numbers.add(number)
            lines = body.splitlines()
            definition = f"[^{number}]: {lines[0]}"
            if len(lines) > 1:
                definition += "\n" + "\n".join(f"    {line}" if line else "" for line in lines[1:])
            parts.append(definition)

        markdown = "\n\n".join(parts)
        return f"{markdown}\n" if markdown else ""

    def _render_blocks(self, blocks: Iterable[Any]) -> str:
        return "\n\n".join(rendered for block in blocks if (rendered := self._render_block(block)))

    def _render_block(self, block: Any) -> str:
        kind = block.kind
        if kind == "heading":
            text = self._render_inlines(block.content or [], context="heading").strip()
            if not text:
                return ""
            level = min(max(int(block.level or 1), 1), 6)
            return f"{'#' * level} {text}"
        if kind == "paragraph":
            return self._render_inlines(block.content or [], context="block").strip()
        if kind == "list":
            return self._render_list(block.list)
        if kind == "table":
            table = block.table
            if table.kind == "layout" and self._table_is_single_cell(table):
                return self._render_blocks(table.grid[0][0].cell.blocks)
            return self._render_table(table)
        if kind == "block_quote":
            inner = self._render_blocks(block.blocks or [])
            if not inner:
                return ""
            if self.source_format == "pptx":
                return f"### Speaker Notes\n\n{inner}"
            return "\n".join(">" if not line else f"> {line}" for line in inner.splitlines())
        if kind == "code_block":
            body = (block.text or "").rstrip("\n")
            fence = self._backtick_fence(body, 3)
            return f"{fence}{block.lang or ''}\n{body}\n{fence}"
        if kind == "rule":
            return "---"
        raise RuntimeError(f"Unsupported AnyDoc block kind: {kind}")

    def _render_inlines(
        self, inlines: Iterable[Any], *, context: str, in_label: bool = False
    ) -> str:
        normalized = self._normalize_inlines(inlines)
        parts: list[str] = []
        for index, inline in enumerate(normalized):
            if isinstance(inline, _TextRun):
                next_inline = normalized[index + 1] if index + 1 < len(normalized) else None
                trailing_active = (
                    isinstance(next_inline, _TextRun)
                    and self._style_key(next_inline.style) != self._style_key(None)
                ) or (
                    next_inline is not None
                    and not isinstance(next_inline, _TextRun)
                    and next_inline.kind in {"link", "image", "note_ref"}
                )
                rendered_so_far = "".join(parts)
                parts.append(
                    self._render_text(
                        inline.text or "",
                        inline.style,
                        context=context,
                        in_label=in_label,
                        trailing_active=trailing_active,
                        at_line_start=not rendered_so_far or rendered_so_far.endswith("\n"),
                    )
                )
                continue

            kind = inline.kind
            if kind == "link":
                target = inline.target
                if not target.value:
                    parts.append(self._render_inlines(inline.content or [], context=context))
                    continue
                label = self._render_inlines(inline.content or [], context=context, in_label=True)
                if target.kind == "anchor":
                    resolved = self.anchors.get(target.value)
                    if resolved is None:
                        parts.append(self._render_inlines(inline.content or [], context=context))
                        continue
                    url = f"#{resolved.fragment}"
                elif target.kind in {"external", "relative"}:
                    url = target.value
                else:
                    raise RuntimeError(f"Unsupported AnyDoc link target kind: {target.kind}")
                if label.strip():
                    parts.append(f"[{label}]({self._format_url(url)})")
                elif target.kind != "anchor":
                    escaped = self._escape_text(
                        url, context=context, in_label=True, trailing_active=True
                    )
                    parts.append(f"[{escaped}]({self._format_url(url)})")
            elif kind == "image":
                parts.append(self._render_image(inline, context=context, in_label=in_label))
            elif kind == "anchor":
                resolved = self.anchors.get(inline.anchor)
                if resolved and resolved.emit_html:
                    parts.append(f'<a id="{html.escape(resolved.fragment, quote=True)}"></a>')
            elif kind == "note_ref":
                number = self.note_numbers.get(inline.note_id)
                if number is not None:
                    parts.append(f"[^{number}]")
            elif kind == "line_break":
                parts.append(
                    "\\\n" if context == "block" else "\n" if context == "table_cell" else " "
                )
            else:
                raise RuntimeError(f"Unsupported AnyDoc inline kind: {kind}")
        return "".join(parts)

    def _normalize_inlines(self, inlines: Iterable[Any]) -> list[Any]:
        """Mirror AnyDoc's run normalization before replacing embedded images."""
        normalized: list[Any] = []
        plain_style = self._style_key(None)
        for inline in inlines:
            if inline.kind == "anchor":
                resolved = self.anchors.get(inline.anchor)
                if resolved is None or not resolved.emit_html:
                    continue
            if inline.kind != "text":
                normalized.append(inline)
                continue
            if not inline.text:
                continue

            style = None if inline.text.isspace() else inline.style
            style_key = self._style_key(style)
            if isinstance(normalized[-1] if normalized else None, _TextRun):
                previous = normalized[-1]
                if self._style_key(previous.style) == style_key:
                    previous.text += inline.text
                    continue
            if (
                style_key != plain_style
                and not style_key[3]
                and len(normalized) >= 2
                and isinstance(normalized[-1], _TextRun)
                and normalized[-1].text.isspace()
                and self._style_key(normalized[-1].style) == plain_style
                and isinstance(normalized[-2], _TextRun)
                and self._style_key(normalized[-2].style) == style_key
            ):
                whitespace = normalized.pop().text
                normalized[-1].text += whitespace + inline.text
                continue
            normalized.append(_TextRun(text=inline.text, style=style))
        return normalized

    @staticmethod
    def _style_key(style: Any) -> tuple[bool, bool, bool, bool]:
        if style is None:
            return False, False, False, False
        return style.bold, style.italic, style.strike, style.code

    def _render_text(
        self,
        text: str,
        style: Any,
        *,
        context: str,
        in_label: bool,
        trailing_active: bool,
        at_line_start: bool,
    ) -> str:
        if style is None or not any((style.bold, style.italic, style.strike, style.code)):
            return self._escape_text(
                text,
                context=context,
                in_label=in_label,
                trailing_active=trailing_active,
                at_line_start=at_line_start,
            )

        leading = text[: len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()) :]
        core_end = len(text) - len(trailing) if trailing else len(text)
        core = text[len(leading) : core_end]
        if not core:
            return text
        if style.code:
            flattened = core.replace("\n", " ")
            fence = self._backtick_fence(flattened, 1)
            pad = " " if flattened.startswith("`") or flattened.endswith("`") else ""
            rendered = f"{fence}{pad}{flattened}{pad}{fence}"
        else:
            opening = ""
            if style.strike:
                opening += "~~"
            if style.bold:
                opening += "**"
            if style.italic:
                opening += "*"
            escaped = self._escape_text(
                core,
                context=context,
                styled=True,
                in_label=in_label,
            )
            rendered = f"{opening}{escaped}{opening[::-1]}"
        return f"{leading}{rendered}{trailing}"

    def _render_image(self, inline: Any, *, context: str, in_label: bool) -> str:
        alt = (inline.alt or "").strip()
        source = inline.source
        escaped_alt = self._escape_text(alt, context=context, in_label=True)
        if source.kind == "external":
            return f"![{escaped_alt}]({self._format_url(source.url)})"
        if source.kind == "unavailable":
            self.warnings.append(f"Embedded image is unavailable: {alt or '<no alt text>'}")
            return self._escape_text(alt, context=context, in_label=in_label)
        if source.kind != "asset":
            raise RuntimeError(f"Unsupported AnyDoc image source kind: {source.kind}")

        asset_id = source.asset_id
        self.assets_referenced.add(asset_id)
        image_ref = self._materialize_asset(asset_id)
        if image_ref is None:
            return self._escape_text(alt, context=context, in_label=in_label)
        return f"![{escaped_alt}]({self._format_url(image_ref)})"

    def _materialize_asset(self, asset_id: int) -> Optional[str]:
        if asset_id in self.asset_paths:
            return self.asset_paths[asset_id]
        asset = self.assets.get(asset_id)
        if asset is None:
            self.warnings.append(f"AnyDoc image references missing asset {asset_id}")
            self.asset_paths[asset_id] = None
            return None
        if not asset.media_type.lower().startswith("image/"):
            self.warnings.append(
                f"AnyDoc asset {asset_id} is not an image ({asset.media_type}); kept as alt text"
            )
            self.asset_paths[asset_id] = None
            return None

        origin_suffix = Path(asset.origin_part).suffix.lower()
        extension = origin_suffix or get_preferred_extension(asset.media_type) or ".png"
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = get_preferred_extension(asset.media_type) or ".png"
        display_path = Path(f"anydoc_asset_{asset_id}{extension}")
        if not is_valid_image(asset.data, display_path):
            self.warnings.append(
                f"AnyDoc asset {asset_id} is not an ingestable image ({asset.media_type})"
            )
            self.asset_paths[asset_id] = None
            return None

        try:
            path = self.storage.save_image(
                self.resource_name,
                asset.data,
                filename=f"anydoc_asset_{asset_id}",
                extension=extension,
            )
            relative = path.relative_to(self.storage.media_dir).as_posix()
        except Exception as exc:
            self.warnings.append(f"Failed to save AnyDoc image asset {asset_id}: {exc}")
            self.asset_paths[asset_id] = None
            return None

        self.asset_paths[asset_id] = relative
        self.images_extracted += 1
        return relative

    def _render_list(self, list_model: Any) -> str:
        if list_model is None or not list_model.items:
            return ""
        rendered_items: list[str] = []
        loose = False
        for index, item in enumerate(list_model.items):
            ordinal = int(list_model.start) + index
            if item.marker_label:
                marker = (
                    "- "
                    f"{self._escape_text(item.marker_label, context='block', at_line_start=True)} "
                )
            elif list_model.marker == "bullet":
                marker = "- "
            elif list_model.marker == "decimal":
                marker = f"{ordinal}. "
            else:
                marker = f"- {self._marker_label(list_model.marker, ordinal)} "
            checkbox = "[x] " if item.checked is True else "[ ] " if item.checked is False else ""
            body = self._render_blocks(item.blocks)
            if len(item.blocks) > 1:
                loose = True
            lines = body.splitlines() or [""]
            indent = " " * len(marker)
            rendered = f"{marker}{checkbox}{lines[0]}"
            for line in lines[1:]:
                if not line:
                    loose = True
                    rendered += "\n"
                else:
                    rendered += f"\n{indent}{line}"
            rendered_items.append(rendered)
        return ("\n\n" if loose else "\n").join(rendered_items)

    def _render_table(self, table: Any) -> str:
        if table is None or not table.grid:
            return ""
        width = max((len(row) for row in table.grid), default=0)
        rows: list[list[tuple[str, bool]]] = []
        for row in table.grid:
            rendered_row: list[tuple[str, bool]] = []
            for slot in row:
                if slot.kind == "origin":
                    rendered_row.append((self._render_cell(slot.cell), False))
                elif slot.kind == "covered":
                    rendered_row.append(("", True))
                else:
                    raise RuntimeError(f"Unsupported AnyDoc table slot kind: {slot.kind}")
            rendered_row.extend(("", False) for _ in range(width - len(rendered_row)))
            rows.append(rendered_row)
        while len(rows) > 1 and all(not text and not covered for text, covered in rows[-1]):
            rows.pop()
        width = max(
            (
                max((index + 1 for index, cell in enumerate(row) if cell[0] or cell[1]), default=0)
                for row in rows
            ),
            default=0,
        )
        if width == 0:
            return ""
        rows = [row[:width] for row in rows]
        if table.header_rows >= 1:
            header = [text for text, _ in rows.pop(0)]
        else:
            header = [""] * width
        lines = [self._format_table_row(header), self._format_table_row(["---"] * width)]
        lines.extend(self._format_table_row([text for text, _ in row]) for row in rows)
        return "\n".join(lines)

    def _render_cell(self, cell: Any) -> str:
        if cell is None:
            return ""
        parts: list[str] = []
        for block in cell.blocks:
            kind = block.kind
            if kind == "heading":
                text = self._render_inlines(block.content or [], context="table_cell").strip()
                if text:
                    parts.append(f"**{text}**")
            elif kind == "paragraph":
                text = self._render_inlines(block.content or [], context="table_cell")
                if text.strip():
                    parts.append(text)
            elif kind == "list":
                flattened = self._render_list(block.list).replace("\n", " ").strip()
                if flattened:
                    parts.append(flattened)
            elif kind == "table":
                for row in block.table.grid:
                    values = [
                        self._render_cell(slot.cell) if slot.kind == "origin" else ""
                        for slot in row
                    ]
                    if any(values):
                        parts.append(" / ".join(values))
            elif kind == "block_quote":
                text = self._render_blocks(block.blocks or []).replace("\n", " ").strip()
                if text:
                    parts.append(text)
            elif kind == "code_block":
                text = (block.text or "").strip()
                if text:
                    fence = self._backtick_fence(text, 1)
                    parts.append(f"{fence}{text}{fence}")
            elif kind != "rule":
                raise RuntimeError(f"Unsupported AnyDoc table-cell block kind: {kind}")
        return "<br>".join(line.strip() for line in "<br>".join(parts).splitlines() if line.strip())

    @staticmethod
    def _format_table_row(cells: Iterable[str]) -> str:
        return "|" + "".join(f" {cell} |" for cell in cells)

    @staticmethod
    def _table_is_single_cell(table: Any) -> bool:
        return (
            len(table.grid) == 1 and len(table.grid[0]) == 1 and table.grid[0][0].kind == "origin"
        )

    def _number_notes(self) -> dict[str, int]:
        notes = {note.id: note for note in self.document.notes if note.blocks}
        order: list[str] = []
        seen: set[str] = set()

        def visit_inlines(inlines: Iterable[Any]) -> None:
            for inline in inlines:
                if inline.kind == "note_ref" and inline.note_id in notes:
                    if inline.note_id not in seen:
                        seen.add(inline.note_id)
                        order.append(inline.note_id)
                        visit_blocks(notes[inline.note_id].blocks)
                elif inline.kind == "link":
                    visit_inlines(inline.content or [])

        def visit_blocks(blocks: Iterable[Any]) -> None:
            for block in blocks:
                if block.kind in {"heading", "paragraph"}:
                    visit_inlines(block.content or [])
                elif block.kind == "list":
                    for item in block.list.items:
                        visit_blocks(item.blocks)
                elif block.kind == "table":
                    for row in block.table.grid:
                        for slot in row:
                            if slot.kind == "origin":
                                visit_blocks(slot.cell.blocks)
                elif block.kind == "block_quote":
                    visit_blocks(block.blocks or [])

        visit_blocks(self.document.blocks)
        for note in self.document.notes:
            if note.id in notes and note.id not in seen:
                seen.add(note.id)
                order.append(note.id)
        return {note_id: index for index, note_id in enumerate(order, 1)}

    def _resolve_anchors(self) -> dict[str, _ResolvedAnchor]:
        linked: set[str] = set()
        headings: list[Any] = []
        anchors: list[str] = []

        def visit_inlines(inlines: Iterable[Any]) -> None:
            for inline in inlines:
                if inline.kind == "link":
                    if inline.target.kind == "anchor":
                        linked.add(inline.target.value)
                    visit_inlines(inline.content or [])
                elif inline.kind == "anchor" and inline.anchor:
                    anchors.append(inline.anchor)

        def visit_blocks(blocks: Iterable[Any]) -> None:
            for block in blocks:
                if block.kind == "heading":
                    headings.append(block)
                    visit_inlines(block.content or [])
                elif block.kind == "paragraph":
                    visit_inlines(block.content or [])
                elif block.kind == "list":
                    for item in block.list.items:
                        visit_blocks(item.blocks)
                elif block.kind == "table":
                    for row in block.table.grid:
                        for slot in row:
                            if slot.kind == "origin":
                                visit_blocks(slot.cell.blocks)
                elif block.kind == "block_quote":
                    visit_blocks(block.blocks or [])

        visit_blocks(self.document.blocks)
        for note in self.document.notes:
            visit_blocks(note.blocks)

        resolved: dict[str, _ResolvedAnchor] = {}
        used: set[str] = set()
        for heading in headings:
            plain = self._plain_text(heading.content or [])
            slug = self._claim_anchor(self._gfm_slug(plain), used)
            heading_ids = [heading.anchor] if heading.anchor else []
            heading_ids.extend(self._inline_anchor_ids(heading.content or []))
            for anchor_id in heading_ids:
                resolved.setdefault(anchor_id, _ResolvedAnchor(slug, False))
        for anchor_id in anchors:
            if anchor_id in linked and anchor_id not in resolved:
                fragment = self._claim_anchor(self._sanitize_anchor(anchor_id), used)
                resolved[anchor_id] = _ResolvedAnchor(fragment, True)
        return resolved

    def _plain_text(self, inlines: Iterable[Any]) -> str:
        parts: list[str] = []
        for inline in inlines:
            if inline.kind == "text":
                parts.append(inline.text or "")
            elif inline.kind == "link":
                parts.append(self._plain_text(inline.content or []))
            elif inline.kind == "image":
                parts.append(inline.alt or "")
            elif inline.kind == "line_break":
                parts.append(" ")
        return "".join(parts)

    def _inline_anchor_ids(self, inlines: Iterable[Any]) -> list[str]:
        ids: list[str] = []
        for inline in inlines:
            if inline.kind == "anchor" and inline.anchor:
                ids.append(inline.anchor)
            elif inline.kind == "link":
                ids.extend(self._inline_anchor_ids(inline.content or []))
        return ids

    @staticmethod
    def _claim_anchor(base: str, used: set[str]) -> str:
        if base not in used:
            used.add(base)
            return base
        suffix = 1
        while f"{base}-{suffix}" in used:
            suffix += 1
        claimed = f"{base}-{suffix}"
        used.add(claimed)
        return claimed

    @staticmethod
    def _gfm_slug(text: str) -> str:
        slug = "".join(
            "-" if char == " " else char.lower()
            for char in text.strip()
            if char == " " or char == "-" or char == "_" or char.isalnum()
        )
        return slug or "section"

    @staticmethod
    def _sanitize_anchor(anchor: str) -> str:
        sanitized = re.sub(r"[^a-z0-9_-]+", "-", anchor.lower()).strip("-")
        return sanitized or "anchor"

    @staticmethod
    def _marker_label(marker: str, number: int) -> str:
        if marker in {"lower_alpha", "upper_alpha"}:
            alpha = ""
            remaining = max(number, 1)
            while remaining:
                remaining, offset = divmod(remaining - 1, 26)
                alpha = chr(ord("a") + offset) + alpha
            return f"{alpha.upper() if marker == 'upper_alpha' else alpha}."
        if marker in {"lower_roman", "upper_roman"}:
            remaining = max(number, 1)
            result = ""
            for unit, symbol in (
                (1000, "M"),
                (900, "CM"),
                (500, "D"),
                (400, "CD"),
                (100, "C"),
                (90, "XC"),
                (50, "L"),
                (40, "XL"),
                (10, "X"),
                (9, "IX"),
                (5, "V"),
                (4, "IV"),
                (1, "I"),
            ):
                while remaining >= unit:
                    result += symbol
                    remaining -= unit
            return f"{result.lower() if marker == 'lower_roman' else result}."
        raise RuntimeError(f"Unsupported AnyDoc list marker: {marker}")

    @staticmethod
    def _format_url(url: str) -> str:
        escaped = "".join(
            "%7C" if char == "|" else "%3C" if char == "<" else "%3E" if char == ">" else char
            for char in url
            if ord(char) >= 32 and ord(char) != 127
        )
        return (
            f"<{escaped}>" if any(char.isspace() or char in "()" for char in escaped) else escaped
        )

    @staticmethod
    def _backtick_fence(text: str, minimum: int) -> str:
        longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
        return "`" * max(longest + 1, minimum)

    @staticmethod
    def _escape_text(
        text: str,
        *,
        context: str,
        styled: bool = False,
        trailing_active: bool = False,
        in_label: bool = False,
        at_line_start: bool = False,
    ) -> str:
        characters = list(text)
        output: list[str] = []
        line_has_content = not at_line_start
        for index, char in enumerate(characters):
            if char == "\n":
                output.append(char)
                if context == "block":
                    line_has_content = False
                continue
            start_of_line = not line_has_content
            if not char.isspace():
                line_has_content = True
            next_char = characters[index + 1] if index + 1 < len(characters) else None
            next_nonspace = trailing_active if next_char is None else not next_char.isspace()
            later = characters[index + 1 :]
            escape = False
            if char == "\\":
                escape = True
            elif char == "]" and in_label:
                escape = True
            elif char == "`":
                escape = styled or "`" in later
            elif char == "*":
                escape = styled or start_of_line or (next_nonspace and "*" in later)
            elif char == "_":
                previous_alnum = index > 0 and characters[index - 1].isalnum()
                next_alnum = bool(next_char and next_char.isalnum())
                escape = styled or (
                    next_nonspace and not (previous_alnum and next_alnum) and "_" in later
                )
            elif char == "~":
                escape = styled or (next_nonspace and "~" in later)
            elif char == "[":
                escape = in_label or "]" in later
            elif char == "<":
                escape = bool(next_char and (next_char.isalpha() or next_char in "/!?"))
            elif char == "!":
                escape = next_char is None and trailing_active
            elif char == "|" and context == "table_cell":
                escape = True
            elif char in "#>" and start_of_line:
                escape = True
            elif char in "-+" and start_of_line:
                escape = next_char is None or next_char.isspace()
            if escape:
                output.append("\\")
            output.append(char)
        return "".join(output)


class AnyDocParser(BaseParser):
    """Parse the existing Office/EPUB formats through AnyDoc's document model."""

    _SUPPORTED_EXTENSIONS = [".doc", ".docx", ".pptx", ".xls", ".xlsx", ".xlsm", ".epub"]

    def __init__(self, config: Optional[ParserConfig] = None):
        from openviking.parse.parsers.markdown import MarkdownParser

        self.config = config or ParserConfig()
        self._markdown_parser = MarkdownParser(config=self.config)

    @property
    def supported_extensions(self) -> List[str]:
        return list(self._SUPPORTED_EXTENSIONS)

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        started = time.time()
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {path}")

        from openviking_cli.utils.storage import get_storage

        storage = get_storage()
        resource_name = kwargs.get("resource_name") or kwargs.get("source_name") or path.stem
        rendered = await asyncio.to_thread(
            self._convert,
            path,
            resource_name=resource_name,
            storage=storage,
        )

        markdown_kwargs = dict(kwargs)
        caller_media_dirs = list(markdown_kwargs.pop("allowed_media_dirs", None) or [])
        caller_media_dirs.append(storage.media_dir)
        result = await self._markdown_parser.parse_content(
            rendered.markdown,
            source_path=str(path),
            instruction=instruction,
            base_dir=path.parent,
            allowed_media_dirs=caller_media_dirs,
            **markdown_kwargs,
        )
        result.source_format = path.suffix.lower().lstrip(".")
        result.parser_name = "AnyDocParser"
        result.parser_version = "1.0"
        result.parse_time = time.time() - started
        result.warnings.extend(rendered.warnings)
        result.meta.update(
            {
                "library": "firecrawl-anydoc",
                "library_version": version("firecrawl-anydoc"),
                "detected_format": rendered.detected_format,
                "assets_referenced": rendered.assets_referenced,
                "images_extracted": rendered.images_extracted,
                "intermediate_markdown_length": len(rendered.markdown),
            }
        )
        return result

    def _convert(self, path: Path, *, resource_name: str, storage: Any) -> _RenderedDocument:
        import anydoc

        data = path.read_bytes()
        detected_format = anydoc.format_from_bytes(data)
        selected_format = detected_format or anydoc.format_from_extension(path.suffix)
        if selected_format is None:
            raise anydoc.UnsupportedError(f"Unrecognized document format: {path.name}")
        document = anydoc.to_document(data, selected_format)
        renderer = _AnyDocMarkdownRenderer(
            document,
            source_format=path.suffix.lower().lstrip("."),
            resource_name=resource_name,
            storage=storage,
        )
        markdown = renderer.render()
        if not markdown.strip():
            raise anydoc.MalformedError(f"No meaningful content extracted from {path.name}")
        return _RenderedDocument(
            markdown=markdown,
            detected_format=detected_format or selected_format,
            warnings=renderer.warnings,
            assets_referenced=len(renderer.assets_referenced),
            images_extracted=renderer.images_extracted,
        )

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        raise NotImplementedError(
            "AnyDocParser requires a document file path; string content is not supported"
        )
