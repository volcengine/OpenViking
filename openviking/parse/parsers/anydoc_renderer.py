"""Render AnyDoc document models into Markdown with OV media rewrites."""

from __future__ import annotations

import html
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openviking.parse.image_validation import is_valid_image
from openviking.parse.parsers.anydoc_adapter import asset_id, attr
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_PRESENTATION_FORMATS = {"ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "pot", "odp"}


@dataclass(frozen=True)
class _ResolvedAnchor:
    fragment: str
    emit_html: bool


@dataclass
class _TextRun:
    text: str
    style: Any


def _kind(value: Any) -> str:
    raw = attr(value, "kind", "type", default="")
    raw = attr(raw, "value", "name", default=raw)
    return str(raw).split(".")[-1].replace("_", "").replace("-", "").lower()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


class _AnyDocMarkdownRenderer:
    """Render AnyDoc's public document model while materializing image assets."""

    def __init__(
        self,
        document: Any,
        *,
        source_format: str,
        resource_name: str,
        storage: Any,
    ):
        self.document = document
        self.source_format = str(source_format or "").lower().removeprefix("format.")
        self.resource_name = resource_name
        self.storage = storage
        self.images_saved = 0
        self.assets: dict[int, Any] = {}
        self.asset_paths: dict[int, str | None] = {}
        self.assets_referenced: set[int] = set()
        self.warnings: list[str] = []
        self._collect_assets()
        self.note_numbers = self._number_notes()
        self.anchors = self._resolve_anchors()

    def _collect_assets(self) -> None:
        self.assets = {}
        for index, asset in enumerate(_as_list(attr(self.document, "assets", default=[]))):
            raw_id = attr(asset, "id", "asset_id", "assetId", default=index)
            try:
                self.assets[int(attr(raw_id, "id", default=raw_id))] = asset
            except (TypeError, ValueError):
                self.assets[index] = asset

    def render(self) -> str:
        parts = [
            part
            for block in _as_list(attr(self.document, "blocks", default=[]))
            if (part := self._render_block(block))
        ]
        parts.extend(self._render_note_definitions())
        markdown = "\n\n".join(parts)
        return f"{markdown}\n" if markdown else ""

    def _render_blocks(self, blocks: Any) -> str:
        return "\n\n".join(
            rendered for block in _as_list(blocks) if (rendered := self._render_block(block))
        )

    def _render_block(self, block: Any) -> str:
        kind = _kind(block)
        content = attr(block, "content", "inlines", default=[])

        if kind == "heading":
            text = self._render_inlines(content, context="heading").strip()
            if not text:
                return ""
            try:
                level = min(max(int(attr(block, "level", default=1) or 1), 1), 6)
            except (TypeError, ValueError):
                level = 1
            return f"{'#' * level} {text}"
        if kind == "paragraph":
            return self._render_inlines(content, context="block").strip()
        if kind == "list":
            return self._render_list(attr(block, "list", default=None) or block)
        if kind == "table":
            table = attr(block, "table", default=None) or block
            if _kind(table) == "layout" and self._table_is_single_cell(table):
                first_cell = attr(_as_list(attr(table, "grid", default=[]))[0][0], "cell")
                return self._render_blocks(
                    attr(first_cell, "blocks", "children", "content", default=[])
                )
            return self._render_table(table)
        if kind == "blockquote":
            inner = self._render_blocks(attr(block, "blocks", "children", "content", default=[]))
            if not inner:
                return ""
            if self.source_format in _PRESENTATION_FORMATS:
                return f"### Speaker Notes\n\n{inner}"
            return "\n".join(">" if not line else f"> {line}" for line in inner.splitlines())
        if kind == "codeblock":
            language = str(attr(block, "language", "lang", default="") or "")
            code = attr(block, "code", "text", "value", default=None)
            if code is None:
                code = self._render_inlines(content, context="block")
            body = str(code).rstrip("\n")
            fence = self._backtick_fence(body, 3)
            return f"{fence}{language}\n{body}\n{fence}"
        if kind in {"rule", "thematicbreak", "horizontalrule"}:
            return "---"

        nested = attr(block, "blocks", "children", default=None)
        if nested is not None:
            return self._render_blocks(nested)
        if content:
            return self._render_inlines(content, context="block")
        text = attr(block, "text", "value", default=None)
        if text is not None:
            return self._escape_text(str(text), context="block")
        raise RuntimeError(f"Unsupported AnyDoc block kind: {kind}")

    def _render_inlines(
        self,
        inlines: Any,
        *,
        context: str,
        in_label: bool = False,
    ) -> str:
        normalized = self._normalize_inlines(_as_list(inlines))
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
                    and _kind(next_inline) in {"link", "image", "noteref"}
                )
                rendered_so_far = "".join(parts)
                parts.append(
                    self._render_text(
                        inline.text,
                        inline.style,
                        context=context,
                        in_label=in_label,
                        trailing_active=trailing_active,
                        at_line_start=not rendered_so_far or rendered_so_far.endswith("\n"),
                    )
                )
                continue

            kind = _kind(inline)
            if kind == "link":
                parts.append(self._render_link(inline, context=context))
            elif kind == "image":
                parts.append(self._render_image(inline, context=context, in_label=in_label))
            elif kind == "anchor":
                anchor = attr(inline, "anchor", "id", "name", default="")
                resolved = self.anchors.get(anchor)
                if resolved and resolved.emit_html:
                    parts.append(f'<a id="{html.escape(resolved.fragment, quote=True)}"></a>')
            elif kind == "noteref":
                note_id = attr(inline, "note_id", "noteId", "id", "label", default="")
                number = self.note_numbers.get(str(note_id))
                if number is not None:
                    parts.append(f"[^{number}]")
            elif kind in {"linebreak", "softbreak", "hardbreak"}:
                parts.append(
                    "\\\n" if context == "block" else "\n" if context == "table_cell" else " "
                )
            else:
                content = attr(inline, "content", "inlines", "children", default=None)
                if content is not None:
                    parts.append(self._render_inlines(content, context=context, in_label=in_label))
                else:
                    text = attr(inline, "text", "value", default=None)
                    if text is None:
                        raise RuntimeError(f"Unsupported AnyDoc inline kind: {kind}")
                    parts.append(
                        self._escape_text(
                            str(text),
                            context=context,
                            in_label=in_label,
                        )
                    )
        return "".join(parts)

    def _normalize_inlines(self, inlines: Iterable[Any]) -> list[Any]:
        normalized: list[Any] = []
        plain_style = self._style_key(None)
        for inline in inlines:
            if isinstance(inline, str):
                inline = {"kind": "text", "text": inline}

            if _kind(inline) == "anchor":
                anchor = attr(inline, "anchor", "id", "name", default="")
                resolved = self.anchors.get(anchor)
                if resolved is None or not resolved.emit_html:
                    continue
            if _kind(inline) != "text":
                normalized.append(inline)
                continue

            text = str(attr(inline, "text", "value", "content", default="") or "")
            if not text:
                continue
            style = None if text.isspace() else self._style_from_inline(inline)
            style_key = self._style_key(style)
            previous = normalized[-1] if normalized else None
            if isinstance(previous, _TextRun) and self._style_key(previous.style) == style_key:
                previous.text += text
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
                normalized[-1].text += whitespace + text
                continue
            normalized.append(_TextRun(text=text, style=style))
        return normalized

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
        bold, italic, strike, code = self._style_key(style)
        if not any((bold, italic, strike, code)):
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
        if code:
            flattened = core.replace("\n", " ")
            fence = self._backtick_fence(flattened, 1)
            pad = " " if flattened.startswith("`") or flattened.endswith("`") else ""
            rendered = f"{fence}{pad}{flattened}{pad}{fence}"
        else:
            opening = ""
            if strike:
                opening += "~~"
            if bold:
                opening += "**"
            if italic:
                opening += "*"
            escaped = self._escape_text(
                core,
                context=context,
                styled=True,
                in_label=in_label,
            )
            rendered = f"{opening}{escaped}{opening[::-1]}"
        return f"{leading}{rendered}{trailing}"

    def _render_link(self, inline: Any, *, context: str) -> str:
        target = attr(inline, "target", default=None)
        label = self._render_inlines(
            attr(inline, "content", "inlines", "children", default=[]),
            context=context,
            in_label=True,
        )
        if not label:
            label = self._escape_text(
                str(attr(inline, "text", "label", default="") or ""),
                context=context,
                in_label=True,
            )

        target_kind = _kind(target)
        target_value = attr(
            target,
            "value",
            "url",
            "href",
            default=attr(inline, "url", "href", "destination", default=""),
        )
        if not target_value:
            return label
        if target_kind == "anchor":
            resolved = self.anchors.get(str(target_value))
            if resolved is None:
                return label
            url = f"#{resolved.fragment}"
        elif target_kind in {"external", "relative", ""}:
            url = str(target_value)
        else:
            raise RuntimeError(f"Unsupported AnyDoc link target kind: {target_kind}")
        if label.strip():
            return f"[{label}]({self._format_url(url)})"
        escaped = self._escape_text(url, context=context, in_label=True, trailing_active=True)
        return f"[{escaped}]({self._format_url(url)})"

    def _render_image(self, image: Any, *, context: str, in_label: bool) -> str:
        alt = str(attr(image, "alt", "alt_text", "altText", default="") or "").strip()
        escaped_alt = self._escape_text(alt, context=context, in_label=True)
        source = attr(image, "source")
        source_kind = _kind(source)

        if source_kind == "external":
            url = attr(source, "url", "href", "src", default="")
            return f"![{escaped_alt}]({self._format_url(str(url))})" if url else escaped_alt
        if source_kind == "unavailable":
            self.warnings.append(f"Embedded image is unavailable: {alt or '<no alt text>'}")
            return self._escape_text(alt, context=context, in_label=in_label)
        if source_kind != "asset":
            return self._escape_text(alt, context=context, in_label=in_label)

        reference = asset_id(source)
        if reference is None:
            self.warnings.append("AnyDoc image references an asset without an id")
            return self._escape_text(alt, context=context, in_label=in_label)
        self.assets_referenced.add(reference)
        image_ref = self._materialize_asset(reference)
        if image_ref is None:
            return self._escape_text(alt, context=context, in_label=in_label)
        return f"![{escaped_alt or f'anydoc_asset_{reference}'}]({self._format_url(image_ref)})"

    def _materialize_asset(self, reference: int) -> str | None:
        if reference in self.asset_paths:
            return self.asset_paths[reference]
        asset = self.assets.get(reference)
        if asset is None:
            self.warnings.append(f"AnyDoc image references missing asset {reference}")
            self.asset_paths[reference] = None
            return None

        media_type = str(
            attr(asset, "media_type", "mediaType", "mime_type", "mimeType", default="") or ""
        )
        if not media_type.lower().startswith("image/"):
            self.warnings.append(
                f"AnyDoc asset {reference} is not an image ({media_type}); kept as alt text"
            )
            self.asset_paths[reference] = None
            return None

        image_data = attr(asset, "bytes", "data", "content", default=None)
        if image_data is None:
            self.warnings.append(f"AnyDoc image asset {reference} has no bytes")
            self.asset_paths[reference] = None
            return None

        image_bytes = bytes(image_data)
        extension = self._image_extension(asset, media_type)
        filename = f"anydoc_asset_{reference}"
        display_path = Path(f"{filename}{extension}")
        if not is_valid_image(image_bytes, display_path):
            self.warnings.append(
                f"AnyDoc asset {reference} is not an ingestable image ({media_type})"
            )
            self.asset_paths[reference] = None
            return None

        try:
            saved_path = self.storage.save_image(
                self.resource_name,
                image_bytes,
                filename=filename,
                extension=extension,
            )
            relative = Path(saved_path).relative_to(Path(self.storage.media_dir)).as_posix()
        except Exception as exc:
            self.warnings.append(f"Failed to save AnyDoc image asset {reference}: {exc}")
            logger.warning(
                "Failed to save anydoc image asset %s from %s",
                reference,
                self.resource_name,
                exc_info=True,
            )
            self.asset_paths[reference] = None
            return None

        self.images_saved += 1
        self.asset_paths[reference] = relative
        return relative

    def _render_list(self, list_model: Any) -> str:
        items = _as_list(attr(list_model, "items", "children", "content", default=[]))
        if not items:
            return ""
        marker_kind = str(attr(list_model, "marker", default="") or "").lower()
        ordered = bool(attr(list_model, "ordered", "is_ordered", "isOrdered", default=False))
        try:
            start = int(attr(list_model, "start", "start_number", "startNumber", default=1) or 1)
        except (TypeError, ValueError):
            start = 1

        rendered_items: list[str] = []
        loose = False
        for index, item in enumerate(items):
            ordinal = start + index
            marker_label = attr(item, "marker_label", "markerLabel", default=None)
            if marker_label:
                marker = f"- {self._escape_text(str(marker_label), context='block')} "
            elif marker_kind in {"bullet", "unordered", ""} and not ordered:
                marker = "- "
            elif marker_kind in {"decimal", "number", "ordered"} or ordered:
                marker = f"{ordinal}. "
            else:
                marker = f"- {self._marker_label(marker_kind, ordinal)} "

            checked = attr(item, "checked", "is_checked", "isChecked", default=None)
            checkbox = "[x] " if checked is True else "[ ] " if checked is False else ""
            body = self._render_blocks(attr(item, "blocks", "children", "content", default=item))
            item_blocks = _as_list(attr(item, "blocks", "children", "content", default=[]))
            if len(item_blocks) > 1:
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
        rows = self._table_rows(table)
        if not rows:
            return ""
        if _kind(table) == "layout" and self._table_is_single_cell(table):
            return self._render_blocks(
                attr(rows[0][0], "blocks", "children", "content", default=[])
            )

        width = max((len(row) for row in rows), default=0)
        rendered_rows: list[list[tuple[str, bool]]] = []
        for row in rows:
            rendered_row: list[tuple[str, bool]] = []
            for slot in row:
                slot_kind = _kind(slot)
                if slot_kind == "origin":
                    rendered_row.append((self._render_cell(attr(slot, "cell", default=None)), False))
                elif slot_kind == "covered":
                    rendered_row.append(("", True))
                elif slot_kind:
                    raise RuntimeError(f"Unsupported AnyDoc table slot kind: {slot_kind}")
                else:
                    rendered_row.append((self._render_cell(slot), False))
            rendered_row.extend(("", False) for _ in range(width - len(rendered_row)))
            rendered_rows.append(rendered_row)

        while len(rendered_rows) > 1 and all(
            not text and not covered for text, covered in rendered_rows[-1]
        ):
            rendered_rows.pop()
        width = max(
            (
                max((index + 1 for index, cell in enumerate(row) if cell[0] or cell[1]), default=0)
                for row in rendered_rows
            ),
            default=0,
        )
        if width == 0:
            return ""
        rendered_rows = [row[:width] for row in rendered_rows]

        try:
            header_rows = int(attr(table, "header_rows", "headerRows", default=1) or 0)
        except (TypeError, ValueError):
            header_rows = 1
        if header_rows >= 1 and rendered_rows:
            header = [text for text, _ in rendered_rows.pop(0)]
        else:
            header = [""] * width
        lines = [self._format_table_row(header), self._format_table_row(["---"] * width)]
        lines.extend(self._format_table_row([text for text, _ in row]) for row in rendered_rows)
        return "\n".join(lines)

    def _table_rows(self, table: Any) -> list[list[Any]]:
        grid = attr(table, "grid", default=None)
        if grid is not None:
            return [list(row) for row in _as_list(grid)]
        rows = _as_list(attr(table, "rows", "content", default=[]))
        return [_as_list(attr(row, "cells", "content", default=row)) for row in rows]

    def _render_cell(self, cell: Any) -> str:
        if cell is None:
            return ""
        parts: list[str] = []
        for block in _as_list(attr(cell, "blocks", "children", "content", default=cell)):
            kind = _kind(block)
            if kind == "heading":
                text = self._render_inlines(
                    attr(block, "content", "inlines", default=[]),
                    context="table_cell",
                ).strip()
                if text:
                    parts.append(f"**{text}**")
            elif kind == "paragraph":
                text = self._render_inlines(
                    attr(block, "content", "inlines", default=[]),
                    context="table_cell",
                )
                if text.strip():
                    parts.append(text)
            elif kind == "list":
                flattened = self._render_list(attr(block, "list", default=None) or block)
                flattened = flattened.replace("\n", " ").strip()
                if flattened:
                    parts.append(flattened)
            elif kind == "table":
                for row in self._table_rows(attr(block, "table", default=None) or block):
                    values = [
                        self._render_cell(attr(slot, "cell", default=None))
                        if _kind(slot) == "origin"
                        else ""
                        for slot in row
                    ]
                    if any(values):
                        parts.append(" / ".join(values))
            elif kind == "blockquote":
                text = self._render_blocks(
                    attr(block, "blocks", "children", "content", default=[])
                ).replace("\n", " ").strip()
                if text:
                    parts.append(text)
            elif kind == "codeblock":
                text = str(attr(block, "code", "text", "value", default="") or "").strip()
                if text:
                    fence = self._backtick_fence(text, 1)
                    pad = " " if text.startswith("`") or text.endswith("`") else ""
                    parts.append(f"{fence}{pad}{text}{pad}{fence}")
            elif kind in {"rule", "thematicbreak", "horizontalrule"}:
                continue
            elif kind:
                text = attr(block, "text", "value", default=None)
                if text is None:
                    raise RuntimeError(f"Unsupported AnyDoc table-cell block kind: {kind}")
                parts.append(self._escape_text(str(text), context="table_cell"))
            else:
                parts.append(self._render_inlines(block, context="table_cell"))
        joined = "<br>".join(parts)
        return "<br>".join(line.strip() for line in joined.splitlines() if line.strip())

    @staticmethod
    def _format_table_row(cells: Iterable[str]) -> str:
        return "|" + "".join(f" {cell} |" for cell in cells)

    def _render_note_definitions(self) -> list[str]:
        rendered: list[str] = []
        emitted: set[int] = set()
        notes_by_id = self._notes_by_id()
        ordered = sorted(self.note_numbers.items(), key=lambda item: item[1])
        for note_id, number in ordered:
            if number in emitted:
                continue
            note = notes_by_id.get(note_id)
            if note is None:
                continue
            body = self._render_blocks(attr(note, "blocks", "content", "children", default=[]))
            if not body:
                continue
            emitted.add(number)
            lines = body.splitlines()
            definition = f"[^{number}]: {lines[0]}"
            if len(lines) > 1:
                definition += "\n" + "\n".join(f"    {line}" if line else "" for line in lines[1:])
            rendered.append(definition)
        return rendered

    def _number_notes(self) -> dict[str, int]:
        notes = {
            str(attr(note, "id", "note_id", "noteId", "label", default=index)): note
            for index, note in enumerate(_as_list(attr(self.document, "notes", default=[])), start=1)
            if attr(note, "blocks", "content", "children", default=None)
        }
        order: list[str] = []
        seen: set[str] = set()

        def visit_inlines(inlines: Any) -> None:
            for inline in _as_list(inlines):
                if _kind(inline) == "noteref":
                    note_id = str(attr(inline, "note_id", "noteId", "id", "label", default=""))
                    if note_id in notes and note_id not in seen:
                        seen.add(note_id)
                        order.append(note_id)
                        visit_blocks(
                            attr(notes[note_id], "blocks", "content", "children", default=[])
                        )
                elif _kind(inline) == "link":
                    visit_inlines(attr(inline, "content", "inlines", "children", default=[]))

        def visit_blocks(blocks: Any) -> None:
            for block in _as_list(blocks):
                kind = _kind(block)
                if kind in {"heading", "paragraph"}:
                    visit_inlines(attr(block, "content", "inlines", default=[]))
                elif kind == "list":
                    for item in _as_list(
                        attr(
                            attr(block, "list", default=None) or block,
                            "items",
                            "children",
                            "content",
                            default=[],
                        )
                    ):
                        visit_blocks(attr(item, "blocks", "children", "content", default=[]))
                elif kind == "table":
                    for row in self._table_rows(attr(block, "table", default=None) or block):
                        for slot in row:
                            if _kind(slot) == "origin":
                                visit_blocks(
                                    attr(
                                        attr(slot, "cell", default=None),
                                        "blocks",
                                        "children",
                                        "content",
                                        default=[],
                                    )
                                )
                elif kind == "blockquote":
                    visit_blocks(attr(block, "blocks", "children", "content", default=[]))

        visit_blocks(attr(self.document, "blocks", default=[]))
        for note_id in notes:
            if note_id not in seen:
                seen.add(note_id)
                order.append(note_id)
        return {note_id: index for index, note_id in enumerate(order, 1)}

    def _notes_by_id(self) -> dict[str, Any]:
        return {
            str(attr(note, "id", "note_id", "noteId", "label", default=index)): note
            for index, note in enumerate(_as_list(attr(self.document, "notes", default=[])), start=1)
        }

    def _resolve_anchors(self) -> dict[str, _ResolvedAnchor]:
        linked: set[str] = set()
        headings: list[Any] = []
        anchors: list[str] = []

        def visit_inlines(inlines: Any) -> None:
            for inline in _as_list(inlines):
                kind = _kind(inline)
                if kind == "link":
                    target = attr(inline, "target", default=None)
                    if _kind(target) == "anchor":
                        value = attr(target, "value", "anchor", "id", default="")
                        if value:
                            linked.add(str(value))
                    visit_inlines(attr(inline, "content", "inlines", "children", default=[]))
                elif kind == "anchor":
                    anchor = attr(inline, "anchor", "id", "name", default="")
                    if anchor:
                        anchors.append(str(anchor))

        def visit_blocks(blocks: Any) -> None:
            for block in _as_list(blocks):
                kind = _kind(block)
                if kind == "heading":
                    headings.append(block)
                    visit_inlines(attr(block, "content", "inlines", default=[]))
                elif kind == "paragraph":
                    visit_inlines(attr(block, "content", "inlines", default=[]))
                elif kind == "list":
                    for item in _as_list(
                        attr(
                            attr(block, "list", default=None) or block,
                            "items",
                            "children",
                            "content",
                            default=[],
                        )
                    ):
                        visit_blocks(attr(item, "blocks", "children", "content", default=[]))
                elif kind == "table":
                    for row in self._table_rows(attr(block, "table", default=None) or block):
                        for slot in row:
                            if _kind(slot) == "origin":
                                visit_blocks(
                                    attr(
                                        attr(slot, "cell", default=None),
                                        "blocks",
                                        "children",
                                        "content",
                                        default=[],
                                    )
                                )
                elif kind == "blockquote":
                    visit_blocks(attr(block, "blocks", "children", "content", default=[]))

        visit_blocks(attr(self.document, "blocks", default=[]))
        for note in _as_list(attr(self.document, "notes", default=[])):
            visit_blocks(attr(note, "blocks", "content", "children", default=[]))

        resolved: dict[str, _ResolvedAnchor] = {}
        used: set[str] = set()
        for heading in headings:
            plain = self._plain_text(attr(heading, "content", "inlines", default=[]))
            slug = self._claim_anchor(self._gfm_slug(plain), used)
            heading_ids = []
            heading_anchor = attr(heading, "anchor", "id", default="")
            if heading_anchor:
                heading_ids.append(str(heading_anchor))
            heading_ids.extend(
                self._inline_anchor_ids(attr(heading, "content", "inlines", default=[]))
            )
            for anchor_id in heading_ids:
                resolved.setdefault(anchor_id, _ResolvedAnchor(slug, False))
        for anchor_id in anchors:
            if anchor_id in linked and anchor_id not in resolved:
                fragment = self._claim_anchor(self._sanitize_anchor(anchor_id), used)
                resolved[anchor_id] = _ResolvedAnchor(fragment, True)
        return resolved

    def _plain_text(self, inlines: Any) -> str:
        parts: list[str] = []
        for inline in _as_list(inlines):
            kind = _kind(inline)
            if kind == "text":
                parts.append(str(attr(inline, "text", "value", "content", default="") or ""))
            elif kind == "link":
                parts.append(
                    self._plain_text(attr(inline, "content", "inlines", "children", default=[]))
                )
            elif kind == "image":
                parts.append(str(attr(inline, "alt", "alt_text", "altText", default="") or ""))
            elif kind in {"linebreak", "softbreak", "hardbreak"}:
                parts.append(" ")
        return "".join(parts)

    def _inline_anchor_ids(self, inlines: Any) -> list[str]:
        ids: list[str] = []
        for inline in _as_list(inlines):
            kind = _kind(inline)
            if kind == "anchor":
                anchor = attr(inline, "anchor", "id", "name", default="")
                if anchor:
                    ids.append(str(anchor))
            elif kind == "link":
                ids.extend(
                    self._inline_anchor_ids(
                        attr(inline, "content", "inlines", "children", default=[])
                    )
                )
        return ids

    @staticmethod
    def _table_is_single_cell(table: Any) -> bool:
        grid = _as_list(attr(table, "grid", default=[]))
        return len(grid) == 1 and len(grid[0]) == 1 and _kind(grid[0][0]) == "origin"

    @staticmethod
    def _style_from_inline(inline: Any) -> Any:
        return attr(inline, "style", "styles", "marks", default=inline)

    @staticmethod
    def _style_key(style: Any) -> tuple[bool, bool, bool, bool]:
        if style is None:
            return False, False, False, False
        direct = (
            bool(attr(style, "bold", "strong", default=False)),
            bool(attr(style, "italic", "emphasis", default=False)),
            bool(attr(style, "strike", "strikethrough", default=False)),
            bool(attr(style, "code", "inline_code", "inlineCode", default=False)),
        )
        if any(direct):
            return direct
        style_names = {
            _kind(item) or str(item).split(".")[-1].replace("_", "").lower()
            for item in _as_list(style)
        }
        return (
            "bold" in style_names or "strong" in style_names,
            "italic" in style_names or "emphasis" in style_names,
            "strike" in style_names or "strikethrough" in style_names,
            "code" in style_names or "inlinecode" in style_names,
        )

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

    @staticmethod
    def _image_extension(asset: Any, media_type: str) -> str:
        extension = mimetypes.guess_extension(media_type.split(";", 1)[0].strip())
        if extension == ".jpe":
            extension = ".jpg"
        if not extension:
            origin = str(
                attr(asset, "origin_part", "originPart", "filename", "name", default="") or ""
            )
            extension = Path(origin).suffix.lower()
        if not extension or not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".png"
        return extension if extension.startswith(".") else f".{extension}"
