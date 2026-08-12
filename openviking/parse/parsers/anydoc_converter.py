"""Shared anydoc → Markdown converter with OV media rewrite."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking.parse.parsers.anydoc_adapter import asset_id, attr
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AnydocConversionResult:
    markdown: str
    images_saved: int
    source_format: str


def _format_name(value: Any) -> str | None:
    if value is None:
        return None
    name = attr(value, "value", "name", default=value)
    return str(name).split(".")[-1].lower()


def _load_document(path: Path, format_hint: str | None = None):
    """Load an anydoc document while keeping the import optional for unit tests."""
    if path.suffix.lower() == ".pdf" or _format_name(format_hint) == "pdf":
        raise ValueError("AnyDocConverter does not support PDF; use PDFParser")

    import anydoc

    data = path.read_bytes()
    detected = anydoc.format_from_bytes(data)
    detected_name = _format_name(detected)
    source_format = (
        _format_name(format_hint)
        or detected_name
        or _format_name(anydoc.format_from_extension(path.suffix))
    )
    if source_format == "pdf":
        raise ValueError("AnyDocConverter does not support PDF; use PDFParser")

    try:
        document = (
            anydoc.to_document(data, source_format)
            if source_format
            else anydoc.to_document(data)
        )
    except anydoc.ConvertError as exc:
        format_context = f" as {source_format}" if source_format else ""
        raise type(exc)(f"Failed to convert {path}{format_context}: {exc}") from exc
    return source_format or path.suffix.lstrip(".").lower() or "unknown", document


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


def _escape_text(value: Any, *, table: bool = False) -> str:
    text = str(value or "").replace("[", r"\[").replace("]", r"\]")
    return text.replace("|", r"\|") if table else text


def _escape_alt(value: Any) -> str:
    return _escape_text(value).replace("\n", " ")


class _SerializeCtx:
    def __init__(self, *, resource_name: str, storage: Any):
        self.resource_name = resource_name
        self.storage = storage
        self.images_saved = 0
        self._assets: dict[int, Any] = {}

    def serialize_document(self, document: Any) -> str:
        assets = _as_list(attr(document, "assets", default=[]))
        self._assets = {}
        for index, asset in enumerate(assets):
            raw_id = attr(asset, "id", "asset_id", "assetId", default=index)
            try:
                self._assets[int(attr(raw_id, "id", default=raw_id))] = asset
            except (TypeError, ValueError):
                self._assets[index] = asset

        body = self.serialize_blocks(attr(document, "blocks", default=[]))
        notes = self._serialize_notes(attr(document, "notes", default=[]))
        parts = [part.strip() for part in (body, notes) if part and part.strip()]
        return "\n\n".join(parts) + ("\n" if parts else "")

    def serialize_blocks(self, blocks: Any) -> str:
        rendered = [self.serialize_block(block) for block in _as_list(blocks)]
        return "\n\n".join(part for part in rendered if part)

    def serialize_block(self, block: Any) -> str:
        kind = _kind(block)
        content = attr(block, "content", "inlines", default=[])

        if kind == "heading":
            try:
                level = max(1, min(6, int(attr(block, "level", default=1))))
            except (TypeError, ValueError):
                level = 1
            return f"{'#' * level} {self.serialize_inlines(content)}".rstrip()
        if kind == "paragraph":
            return self.serialize_inlines(content)
        if kind == "list":
            return self._serialize_list(block)
        if kind == "table":
            return self._serialize_table(block)
        if kind == "blockquote":
            nested = self._serialize_nested(
                attr(block, "blocks", "content", "children", default=[])
            )
            return "\n".join(f"> {line}" if line else ">" for line in nested.splitlines())
        if kind == "codeblock":
            language = str(attr(block, "language", "lang", default="") or "")
            code = attr(block, "code", "text", "value", default=None)
            if code is None:
                code = self.serialize_inlines(content)
            return f"```{language}\n{str(code).rstrip()}\n```"
        if kind in {"rule", "thematicbreak", "horizontalrule"}:
            return "---"

        nested = attr(block, "blocks", "children", default=None)
        if nested is not None:
            return self.serialize_blocks(nested)
        return self.serialize_inlines(content)

    def serialize_inlines(self, inlines: Any, *, table: bool = False) -> str:
        return "".join(self._serialize_inline(inline, table=table) for inline in _as_list(inlines))

    def _serialize_inline(self, inline: Any, *, table: bool = False) -> str:
        if isinstance(inline, str):
            return _escape_text(inline, table=table)

        kind = _kind(inline)
        if kind == "text" or not kind:
            text = _escape_text(attr(inline, "text", "value", "content", default=""), table=table)
            return self._apply_text_styles(text, inline)
        if kind == "link":
            label = self.serialize_inlines(
                attr(inline, "content", "inlines", "children", default=[]),
                table=table,
            )
            if not label:
                label = _escape_text(attr(inline, "text", "label", default=""), table=table)
            target = attr(inline, "target", default=None)
            url = attr(
                inline,
                "url",
                "href",
                "destination",
                default=attr(target, "value", "url", "href", default=""),
            )
            return f"[{label}]({url})" if url else label
        if kind == "image":
            return self.emit_image(inline)
        if kind in {"linebreak", "softbreak", "hardbreak"}:
            return "\n"
        if kind in {"noteref", "footnoteref"}:
            note_id = attr(inline, "note_id", "noteId", "id", "label", default="")
            return f"[^{note_id}]" if note_id != "" else ""
        if kind == "anchor":
            anchor = attr(inline, "id", "name", "anchor", default="")
            return f'<a id="{anchor}"></a>' if anchor else ""

        content = attr(inline, "content", "inlines", "children", default=None)
        if content is not None:
            return self.serialize_inlines(content, table=table)
        return _escape_text(attr(inline, "text", "value", default=""), table=table)

    def emit_image(self, image: Any) -> str:
        alt = _escape_alt(attr(image, "alt", "alt_text", "altText", default=""))
        source = attr(image, "source")
        source_kind = _kind(source)

        if source_kind == "external":
            url = attr(source, "url", "href", "src", default="")
            return f"![{alt}]({url})" if url else alt
        if source_kind != "asset":
            return alt

        reference = asset_id(source)
        asset = self._assets.get(reference) if reference is not None else None
        if asset is None:
            logger.debug("Skipping image with missing anydoc asset: %s", reference)
            return alt

        media_type = str(
            attr(asset, "media_type", "mediaType", "mime_type", "mimeType", default="") or ""
        )
        if not media_type.lower().startswith("image/"):
            logger.debug("Skipping non-image anydoc asset with MIME %s", media_type)
            return alt

        image_data = attr(asset, "bytes", "data", "content", default=None)
        if image_data is None:
            logger.debug("Skipping anydoc image asset without bytes: %s", reference)
            return alt

        extension = self._image_extension(asset, media_type)
        image_number = self.images_saved + 1
        filename = f"image{image_number}"
        try:
            saved_path = self.storage.save_image(
                self.resource_name,
                bytes(image_data),
                filename=filename,
                extension=extension,
            )
            relative = Path(saved_path).relative_to(Path(self.storage.media_dir)).as_posix()
        except Exception:
            logger.warning(
                "Failed to save anydoc image asset %s from %s",
                reference,
                self.resource_name,
                exc_info=True,
            )
            return alt
        self.images_saved = image_number
        return f"![{alt or filename}]({relative})"

    def _serialize_list(self, block: Any) -> str:
        list_data = attr(block, "list", default=None) or block
        items = _as_list(attr(list_data, "items", "children", "content", default=[]))
        marker_kind = str(attr(list_data, "marker", default="") or "").lower()
        ordered = bool(
            attr(list_data, "ordered", "is_ordered", "isOrdered", default=False)
        ) or marker_kind in {"decimal", "number", "ordered"}
        try:
            start = int(
                attr(
                    list_data,
                    "start",
                    "start_number",
                    "startNumber",
                    default=1,
                )
            )
        except (TypeError, ValueError):
            start = 1

        lines: list[str] = []
        for index, item in enumerate(items):
            marker = f"{start + index}." if ordered else "-"
            value = attr(item, "blocks", "children", "content", default=item)
            rendered = self._serialize_nested(value)
            item_lines = rendered.splitlines() or [""]
            lines.append(f"{marker} {item_lines[0]}")
            lines.extend(f"  {line}" for line in item_lines[1:])
        return "\n".join(lines)

    def _serialize_table(self, block: Any) -> str:
        table_data = attr(block, "table", default=None) or block
        grid = attr(table_data, "grid", default=None)
        rows = _as_list(
            grid if grid is not None else attr(table_data, "rows", "content", default=[])
        )
        matrix = []
        for row in rows:
            cells = _as_list(
                row if grid is not None else attr(row, "cells", "content", default=row)
            )
            matrix.append(
                [
                    self._serialize_table_cell(
                        attr(slot, "cell", default=None) if grid is not None else slot
                    )
                    for slot in cells
                ]
            )
        if not matrix:
            return ""
        width = max(len(row) for row in matrix)
        matrix = [row + [""] * (width - len(row)) for row in matrix]
        header = matrix[0]
        output = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join("---" for _ in range(width)) + " |",
        ]
        output.extend("| " + " | ".join(row) + " |" for row in matrix[1:])
        return "\n".join(output)

    def _serialize_table_cell(self, cell: Any) -> str:
        value = attr(cell, "blocks", "children", "content", default=cell)
        rendered = self._serialize_nested(value, table=True)
        return rendered.replace("\n", "<br>")

    def _serialize_nested(self, value: Any, *, table: bool = False) -> str:
        values = _as_list(value)
        if any(
            _kind(item)
            in {
                "heading",
                "paragraph",
                "list",
                "table",
                "blockquote",
                "codeblock",
                "rule",
            }
            for item in values
        ):
            if table:
                rendered = [self.serialize_block(item) for item in values]
                return "<br>".join(item.replace("|", r"\|") for item in rendered if item)
            return self.serialize_blocks(values)
        return self.serialize_inlines(values, table=table)

    def _serialize_notes(self, notes: Any) -> str:
        rendered: list[str] = []
        for index, note in enumerate(_as_list(notes), start=1):
            note_id = attr(note, "id", "note_id", "noteId", "label", default=index)
            content = attr(note, "blocks", "content", "children", default=[])
            text = self._serialize_nested(content)
            if text:
                continuation = text.replace("\n", "\n    ")
                rendered.append(f"[^{note_id}]: {continuation}")
        return "## Notes\n\n" + "\n\n".join(rendered) if rendered else ""

    @staticmethod
    def _apply_text_styles(text: str, inline: Any) -> str:
        style = attr(inline, "style", "styles", "marks", default=None)

        def enabled(*names: str) -> bool:
            for name in names:
                direct = attr(inline, name, default=None)
                styled = attr(style, name, default=None)
                if direct is not None or styled is not None:
                    return bool(direct if direct is not None else styled)
            style_names = {
                _kind(item) or str(item).split(".")[-1].lower() for item in _as_list(style)
            }
            return any(name.replace("_", "").lower() in style_names for name in names)

        if enabled("code", "inline_code", "inlineCode"):
            escaped = text.replace("`", "\\`")
            text = f"`{escaped}`"
        if enabled("bold", "strong"):
            text = f"**{text}**"
        if enabled("italic", "emphasis"):
            text = f"*{text}*"
        if enabled("strike", "strikethrough"):
            text = f"~~{text}~~"
        return text

    @staticmethod
    def _image_extension(asset: Any, media_type: str) -> str:
        extension = mimetypes.guess_extension(media_type.split(";", 1)[0].strip())
        if extension == ".jpe":
            extension = ".jpg"
        if not extension:
            origin = str(
                attr(asset, "origin_part", "originPart", "filename", "name", default="") or ""
            )
            extension = Path(origin).suffix
        if not extension:
            extension = ".png"
        return extension if extension.startswith(".") else f".{extension}"


class AnyDocConverter:
    def convert(
        self,
        path: Path,
        *,
        resource_name: str,
        storage: Any,
        format_hint: str | None = None,
    ) -> AnydocConversionResult:
        path = Path(path)
        if path.suffix.lower() == ".pdf" or _format_name(format_hint) == "pdf":
            raise ValueError("AnyDocConverter does not support PDF; use PDFParser")

        source_format, document = _load_document(path, format_hint=format_hint)
        context = _SerializeCtx(resource_name=resource_name, storage=storage)
        markdown = context.serialize_document(document)
        return AnydocConversionResult(
            markdown=markdown,
            images_saved=context.images_saved,
            source_format=str(source_format).lower().removeprefix("format."),
        )
