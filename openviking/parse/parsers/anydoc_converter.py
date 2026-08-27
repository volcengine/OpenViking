"""Shared anydoc -> Markdown converter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer, _attr


@dataclass(frozen=True)
class AnydocConversionResult:
    markdown: str
    images_saved: int
    source_format: str
    assets_referenced: int = 0
    warnings: tuple[str, ...] = ()


def _format_name(value: Any) -> str | None:
    if value is None:
        return None
    name = _attr(value, "value", "name", default=value)
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


class AnyDocConverter:
    def convert(
        self,
        path: Path,
        *,
        resource_name: str,
        storage: Any,
        format_hint: str | None = None,
        max_table_rows: int = 1000,
    ) -> AnydocConversionResult:
        path = Path(path)
        if path.suffix.lower() == ".pdf" or _format_name(format_hint) == "pdf":
            raise ValueError("AnyDocConverter does not support PDF; use PDFParser")

        source_format, document = _load_document(path, format_hint=format_hint)
        renderer = _AnyDocMarkdownRenderer(
            document,
            source_format=source_format,
            resource_name=resource_name,
            storage=storage,
            max_table_rows=max_table_rows,
        )
        markdown = renderer.render()
        return AnydocConversionResult(
            markdown=markdown,
            images_saved=renderer.images_saved,
            source_format=str(source_format).lower().removeprefix("format."),
            assets_referenced=len(renderer.assets_referenced),
            warnings=tuple(renderer.warnings),
        )
