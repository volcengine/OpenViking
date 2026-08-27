"""Render firecrawl-anydoc document models into Markdown with OV media rewrites."""

from __future__ import annotations

import html
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from openviking.parse.image_validation import is_valid_image
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_PRESENTATION_FORMATS = {"ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "pot", "odp"}


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if obj is None:
            break
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _asset_id(source: Any) -> int | None:
    raw = _attr(source, "asset_id", "assetId")
    if raw is None:
        return None
    if hasattr(raw, "id"):
        return int(getattr(raw, "id", raw))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _ResolvedAnchor:
    fragment: str
    emit_html: bool


@dataclass
class _TextRun:
    text: str
    style: Any


def _kind(value: Any) -> str:
    raw = _attr(value, "kind", default="")
    raw = _attr(raw, "value", "name", default=raw)
    return str(raw).split(".")[-1].lower()


def _items(value: list[Any] | None) -> list[Any]:
    return value or []


class _AnyDocMarkdownRenderer:
    """Render AnyDoc's public Document model while materializing image assets."""

    def __init__(
        self,
        document: Any,
        *,
        source_format: str,
        resource_name: str,
        storage: Any,
        max_table_rows: int = 1000,
    ):
        self.document = document
        self.source_format = str(source_format or "").lower().removeprefix("format.")
        self.resource_name = resource_name
        self.storage = storage
        self.max_table_rows = max_table_rows
        self.images_saved = 0
        self.asset_paths: dict[int, str | None] = {}
        self.assets_referenced: set[int] = set()
        self.warnings: list[str] = []
        self.assets = self._collect_assets()
        self.note_numbers = self._number_notes()
        self.anchors = self._resolve_anchors()

    def _collect_assets(self) -> dict[int, Any]:
        assets: dict[int, Any] = {}
        for index, asset in enumerate(_items(self.document.assets)):
            raw_id = _attr(asset.id, "id", default=asset.id)
            try:
                assets[int(raw_id)] = asset
            except (TypeError, ValueError):
                assets[index] = asset
        return assets

    def render(self) -> str:
        parts = [part for block in _items(self.document.blocks) if (part := self._render_block(block))]
        parts.extend(self._render_note_definitions())
        markdown = "\n\n".join(parts)
        return f"{markdown}\n" if markdown else ""

    def _render_blocks(self, blocks: list[Any] | None) -> str:
        return "\n\n".join(
            rendered for block in _items(blocks) if (rendered := self._render_block(block))
        )

    def _render_block(self, block: Any) -> str:
        kind = _kind(block)
        if kind == "heading":
            text = self._render_inlines(block.content, context="heading").strip()
            if not text:
                return ""
            level = min(max(int(block.level or 1), 1), 6)
            return f"{'#' * level} {text}"
        if kind == "paragraph":
            return self._render_inlines(block.content, context="block").strip()
        if kind == "list":
            return self._render_list(block.list)
        if kind == "table":
            if block.table.kind == "layout" and self._table_is_single_cell(block.table):
                cell = block.table.grid[0][0].cell
                return self._render_blocks(cell.blocks if cell else [])
            return self._render_table(block.table)
        if kind == "block_quote":
            inner = self._render_blocks(block.blocks)
            if not inner:
                return ""
            if self.source_format in _PRESENTATION_FORMATS:
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
        self,
        inlines: list[Any] | None,
        *,
        context: str,
        in_label: bool = False,
    ) -> str:
        normalized = self._normalize_inlines(_items(inlines))
        parts: list[str] = []
        for index, inline in enumerate(normalized):
            if isinstance(inline, _TextRun):
                next_inline = normalized[index + 1] if index + 1 < len(normalized) else None
                trailing_active = self._next_inline_is_active(next_inline)
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
            parts.append(self._render_inline(inline, context=context, in_label=in_label))
        return "".join(parts)

    def _render_inline(self, inline: Any, *, context: str, in_label: bool) -> str:
        kind = _kind(inline)
        if kind == "link":
            return self._render_link(inline, context=context)
        if kind == "image":
            return self._render_image(inline, context=context, in_label=in_label)
        if kind == "anchor":
            resolved = self.anchors.get(inline.anchor)
            return f'<a id="{html.escape(resolved.fragment, quote=True)}"></a>' if resolved and resolved.emit_html else ""
        if kind == "note_ref":
            number = self.note_numbers.get(inline.note_id or "")
            return f"[^{number}]" if number is not None else ""
        if kind == "line_break":
            return "\\\n" if context == "block" else "\n" if context == "table_cell" else " "
        raise RuntimeError(f"Unsupported AnyDoc inline kind: {kind}")

    def _normalize_inlines(self, inlines: Iterable[Any]) -> list[Any]:
        normalized: list[Any] = []
        plain_style = self._style_key(None)
        for inline in inlines:
            kind = _kind(inline)
            if kind == "anchor":
                resolved = self.anchors.get(inline.anchor)
                if resolved is None or not resolved.emit_html:
                    continue
            if kind != "text":
                normalized.append(inline)
                continue
            if not inline.text:
                continue

            style = None if inline.text.isspace() else inline.style
            style_key = self._style_key(style)
            previous = normalized[-1] if normalized else None
            if isinstance(previous, _TextRun) and self._style_key(previous.style) == style_key:
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
        bold, italic, strike, code, underline = self._style_key(style)
        if not any((bold, italic, strike, code, underline)):
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
            closing = ""
            if strike:
                opening += "~~"
                closing = "~~" + closing
            if bold:
                opening += "**"
                closing = "**" + closing
            if italic:
                opening += "*"
                closing = "*" + closing
            escaped = self._escape_text(core, context=context, styled=True, in_label=in_label)
            rendered = f"{opening}{escaped}{closing}"
        if underline:
            rendered = f"<ins>{rendered}</ins>"
        return f"{leading}{rendered}{trailing}"

    def _render_link(self, inline: Any, *, context: str) -> str:
        target = inline.target
        label = self._render_inlines(inline.content, context=context, in_label=True)
        if not target or not target.value:
            return label
        if target.kind == "anchor":
            resolved = self.anchors.get(target.value)
            if resolved is None:
                return label
            url = f"#{resolved.fragment}"
        elif target.kind in {"external", "relative"}:
            url = target.value
        else:
            raise RuntimeError(f"Unsupported AnyDoc link target kind: {target.kind}")
        if label.strip():
            return f"[{label}]({self._format_url(url)})"
        escaped = self._escape_text(url, context=context, in_label=True, trailing_active=True)
        return f"[{escaped}]({self._format_url(url)})"

    def _render_image(self, image: Any, *, context: str, in_label: bool) -> str:
        alt = (image.alt or "").strip()
        escaped_alt = self._escape_text(alt, context=context, in_label=True)
        source = image.source
        if source is None:
            return self._escape_text(alt, context=context, in_label=in_label)
        if source.kind == "external":
            return f"![{escaped_alt}]({self._format_url(source.url or '')})" if source.url else escaped_alt
        if source.kind == "unavailable":
            self.warnings.append(f"Embedded image is unavailable: {alt or '<no alt text>'}")
            return self._escape_text(alt, context=context, in_label=in_label)
        if source.kind != "asset":
            return self._escape_text(alt, context=context, in_label=in_label)

        reference = _asset_id(source)
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
        if not asset.media_type.lower().startswith("image/"):
            self.warnings.append(
                f"AnyDoc asset {reference} is not an image ({asset.media_type}); kept as alt text"
            )
            self.asset_paths[reference] = None
            return None

        image_bytes = bytes(asset.data)
        extension = self._image_extension(asset)
        filename = f"anydoc_asset_{reference}"
        if not is_valid_image(image_bytes, Path(f"{filename}{extension}")):
            self.warnings.append(
                f"AnyDoc asset {reference} is not an ingestable image ({asset.media_type})"
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
        if list_model is None or not list_model.items:
            return ""
        rendered_items: list[str] = []
        loose = False
        for index, item in enumerate(list_model.items):
            ordinal = int(list_model.start) + index
            marker = self._list_marker(list_model, item, ordinal)
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
        if table.kind == "layout" and self._table_is_single_cell(table):
            cell = table.grid[0][0].cell
            return self._render_blocks(cell.blocks if cell else [])

        grid = _items(table.grid)
        truncated_rows = 0
        if self.max_table_rows > 0 and len(grid) > self.max_table_rows:
            truncated_rows = len(grid) - self.max_table_rows
            grid = grid[: self.max_table_rows]

        rows = self._render_table_rows(grid)
        while len(rows) > 1 and all(not text and not covered for text, covered in rows[-1]):
            rows.pop()
        width = self._table_width(rows)
        if width == 0:
            return ""
        rows = [row[:width] for row in rows]
        header = [text for text, _ in rows.pop(0)] if table.header_rows >= 1 else [""] * width
        lines = [self._format_table_row(header), self._format_table_row(["---"] * width)]
        lines.extend(self._format_table_row([text for text, _ in row]) for row in rows)
        if truncated_rows:
            lines.append(f"\n*... {truncated_rows} more rows truncated ...*")
        return "\n".join(lines)

    def _render_table_rows(self, grid: list[Any]) -> list[list[tuple[str, bool]]]:
        width = max((len(row) for row in grid), default=0)
        rows: list[list[tuple[str, bool]]] = []
        for row in grid:
            rendered = [
                (self._render_cell(slot.cell), False)
                if slot.kind == "origin"
                else ("", True)
                for slot in row
            ]
            rendered.extend(("", False) for _ in range(width - len(rendered)))
            rows.append(rendered)
        return rows

    def _render_cell(self, cell: Any | None) -> str:
        if cell is None:
            return ""
        parts: list[str] = []
        for block in cell.blocks:
            kind = _kind(block)
            if kind == "heading":
                text = self._render_inlines(block.content, context="table_cell").strip()
                if text:
                    parts.append(f"**{text}**")
            elif kind == "paragraph":
                text = self._render_inlines(block.content, context="table_cell")
                if text.strip():
                    parts.append(text)
            elif kind == "list":
                text = self._render_list(block.list).replace("\n", " ").strip()
                if text:
                    parts.append(text)
            elif kind == "table":
                parts.extend(self._flatten_nested_table(block.table))
            elif kind == "block_quote":
                text = self._render_blocks(block.blocks).replace("\n", " ").strip()
                if text:
                    parts.append(text)
            elif kind == "code_block":
                text = (block.text or "").strip()
                if text:
                    fence = self._backtick_fence(text, 1)
                    pad = " " if text.startswith("`") or text.endswith("`") else ""
                    parts.append(f"{fence}{pad}{text}{pad}{fence}")
            elif kind != "rule":
                raise RuntimeError(f"Unsupported AnyDoc table-cell block kind: {kind}")
        return "<br>".join(line.strip() for line in "<br>".join(parts).splitlines() if line.strip())

    def _flatten_nested_table(self, table: Any) -> list[str]:
        lines: list[str] = []
        for row in table.grid:
            values = [
                self._render_cell(slot.cell) if slot.kind == "origin" else "" for slot in row
            ]
            if any(values):
                lines.append(" / ".join(values))
        return lines

    def _render_note_definitions(self) -> list[str]:
        rendered: list[str] = []
        notes_by_id = {note.id: note for note in _items(self.document.notes)}
        for note_id, number in sorted(self.note_numbers.items(), key=lambda item: item[1]):
            note = notes_by_id.get(note_id)
            if note is None:
                continue
            body = self._render_blocks(note.blocks)
            if not body:
                continue
            lines = body.splitlines()
            definition = f"[^{number}]: {lines[0]}"
            if len(lines) > 1:
                definition += "\n" + "\n".join(f"    {line}" if line else "" for line in lines[1:])
            rendered.append(definition)
        return rendered

    def _number_notes(self) -> dict[str, int]:
        notes = {note.id: note for note in _items(self.document.notes) if note.blocks}
        order: list[str] = []
        seen: set[str] = set()

        def add(note_id: str | None) -> None:
            if note_id and note_id in notes and note_id not in seen:
                seen.add(note_id)
                order.append(note_id)
                for inline in self._walk_inlines(notes[note_id].blocks):
                    if _kind(inline) == "note_ref":
                        add(inline.note_id)

        for inline in self._walk_inlines(self.document.blocks):
            if _kind(inline) == "note_ref":
                add(inline.note_id)
        for note_id in notes:
            add(note_id)
        return {note_id: index for index, note_id in enumerate(order, 1)}

    def _resolve_anchors(self) -> dict[str, _ResolvedAnchor]:
        linked: set[str] = set()
        headings: list[Any] = []
        anchors: list[str] = []

        for block in self._walk_blocks(self.document.blocks):
            if _kind(block) == "heading":
                headings.append(block)
            for inline in self._block_inlines(block):
                kind = _kind(inline)
                if kind == "link" and inline.target and inline.target.kind == "anchor":
                    linked.add(inline.target.value)
                elif kind == "anchor" and inline.anchor:
                    anchors.append(inline.anchor)
        for note in _items(self.document.notes):
            for inline in self._walk_inlines(note.blocks):
                if _kind(inline) == "anchor" and inline.anchor:
                    anchors.append(inline.anchor)

        resolved: dict[str, _ResolvedAnchor] = {}
        used: set[str] = set()
        for heading in headings:
            slug = self._claim_anchor(self._gfm_slug(self._plain_text(heading.content)), used)
            heading_ids = [heading.anchor] if heading.anchor else []
            heading_ids.extend(self._inline_anchor_ids(heading.content))
            for anchor_id in heading_ids:
                resolved.setdefault(anchor_id, _ResolvedAnchor(slug, False))
        for anchor_id in anchors:
            if anchor_id in linked and anchor_id not in resolved:
                fragment = self._claim_anchor(self._sanitize_anchor(anchor_id), used)
                resolved[anchor_id] = _ResolvedAnchor(fragment, True)
        return resolved

    def _walk_blocks(self, blocks: list[Any] | None) -> Iterator[Any]:
        for block in _items(blocks):
            yield block
            kind = _kind(block)
            if kind == "list" and block.list:
                for item in block.list.items:
                    yield from self._walk_blocks(item.blocks)
            elif kind == "table" and block.table:
                for row in block.table.grid:
                    for slot in row:
                        if slot.kind == "origin" and slot.cell:
                            yield from self._walk_blocks(slot.cell.blocks)
            elif kind == "block_quote":
                yield from self._walk_blocks(block.blocks)

    def _walk_inlines(self, blocks: list[Any] | None) -> Iterator[Any]:
        for block in self._walk_blocks(blocks):
            yield from self._block_inlines(block)

    def _block_inlines(self, block: Any) -> Iterator[Any]:
        if _kind(block) not in {"heading", "paragraph"}:
            return
        yield from self._inline_tree(block.content)

    def _inline_tree(self, inlines: list[Any] | None) -> Iterator[Any]:
        for inline in _items(inlines):
            yield inline
            if _kind(inline) == "link":
                yield from self._inline_tree(inline.content)

    def _plain_text(self, inlines: list[Any] | None) -> str:
        parts: list[str] = []
        for inline in _items(inlines):
            kind = _kind(inline)
            if kind == "text":
                parts.append(inline.text or "")
            elif kind == "link":
                parts.append(self._plain_text(inline.content))
            elif kind == "image":
                parts.append(inline.alt or "")
            elif kind == "line_break":
                parts.append(" ")
        return "".join(parts)

    def _inline_anchor_ids(self, inlines: list[Any] | None) -> list[str]:
        ids: list[str] = []
        for inline in _items(inlines):
            kind = _kind(inline)
            if kind == "anchor" and inline.anchor:
                ids.append(inline.anchor)
            elif kind == "link":
                ids.extend(self._inline_anchor_ids(inline.content))
        return ids

    @staticmethod
    def _next_inline_is_active(inline: Any) -> bool:
        if inline is None:
            return False
        return not isinstance(inline, _TextRun) or _AnyDocMarkdownRenderer._style_key(inline.style) != (
            False,
            False,
            False,
            False,
            False,
        )

    @staticmethod
    def _table_is_single_cell(table: Any) -> bool:
        return len(table.grid) == 1 and len(table.grid[0]) == 1 and table.grid[0][0].kind == "origin"

    @staticmethod
    def _table_width(rows: list[list[tuple[str, bool]]]) -> int:
        return max(
            (
                max((index + 1 for index, cell in enumerate(row) if cell[0] or cell[1]), default=0)
                for row in rows
            ),
            default=0,
        )

    @staticmethod
    def _format_table_row(cells: Iterable[str]) -> str:
        return "|" + "".join(f" {cell} |" for cell in cells)

    @staticmethod
    def _style_key(style: Any) -> tuple[bool, bool, bool, bool, bool]:
        if style is None:
            return False, False, False, False, False
        return (
            bool(_attr(style, "bold", default=False)),
            bool(_attr(style, "italic", default=False)),
            bool(_attr(style, "strike", default=False)),
            bool(_attr(style, "code", default=False)),
            bool(_attr(style, "underline", "underlined", default=False)),
        )

    @staticmethod
    def _list_marker(list_model: Any, item: Any, ordinal: int) -> str:
        if item.marker_label:
            return f"- {item.marker_label} "
        if list_model.marker == "bullet":
            return "- "
        if list_model.marker == "decimal":
            return f"{ordinal}. "
        return f"- {_AnyDocMarkdownRenderer._marker_label(list_model.marker, ordinal)} "

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
    def _image_extension(asset: Any) -> str:
        extension = mimetypes.guess_extension(asset.media_type.split(";", 1)[0].strip())
        if extension == ".jpe":
            extension = ".jpg"
        if not extension:
            extension = Path(asset.origin_part or "").suffix.lower()
        if not extension or not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".png"
        return extension if extension.startswith(".") else f".{extension}"
