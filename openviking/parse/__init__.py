# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Document parsers and their public lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openviking.core.building_tree import BuildingTree
    from openviking.parse.base import NodeType, ParseResult, ResourceNode, create_parse_result
    from openviking.parse.directory_scan import (
        CLASS_PROCESSABLE,
        CLASS_UNSUPPORTED,
        ClassifiedFile,
        DirectoryScanResult,
        scan_directory,
    )
    from openviking.parse.mode import ParseMode, ParseModeInput
    from openviking.parse.parsers.base_parser import BaseParser
    from openviking.parse.parsers.html import HTMLParser
    from openviking.parse.parsers.markdown import MarkdownParser
    from openviking.parse.parsers.pdf import PDFParser
    from openviking.parse.parsers.text import TextParser
    from openviking.parse.tree_builder import TreeBuilder
    from openviking.parse.vlm import VLMProcessor

_EXPORTS = {
    "BaseParser": ("openviking.parse.parsers.base_parser", "BaseParser"),
    "ResourceNode": ("openviking.parse.base", "ResourceNode"),
    "NodeType": ("openviking.parse.base", "NodeType"),
    "ParseResult": ("openviking.parse.base", "ParseResult"),
    "create_parse_result": ("openviking.parse.base", "create_parse_result"),
    "TextParser": ("openviking.parse.parsers.text", "TextParser"),
    "MarkdownParser": ("openviking.parse.parsers.markdown", "MarkdownParser"),
    "PDFParser": ("openviking.parse.parsers.pdf", "PDFParser"),
    "HTMLParser": ("openviking.parse.parsers.html", "HTMLParser"),
    "TreeBuilder": ("openviking.parse.tree_builder", "TreeBuilder"),
    "BuildingTree": ("openviking.core.building_tree", "BuildingTree"),
    "VLMProcessor": ("openviking.parse.vlm", "VLMProcessor"),
    "CLASS_PROCESSABLE": ("openviking.parse.directory_scan", "CLASS_PROCESSABLE"),
    "CLASS_UNSUPPORTED": ("openviking.parse.directory_scan", "CLASS_UNSUPPORTED"),
    "ClassifiedFile": ("openviking.parse.directory_scan", "ClassifiedFile"),
    "DirectoryScanResult": ("openviking.parse.directory_scan", "DirectoryScanResult"),
    "scan_directory": ("openviking.parse.directory_scan", "scan_directory"),
    "ParseMode": ("openviking.parse.mode", "ParseMode"),
    "ParseModeInput": ("openviking.parse.mode", "ParseModeInput"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(__all__))


__all__ = [
    "BaseParser",
    "ResourceNode",
    "NodeType",
    "ParseResult",
    "create_parse_result",
    "TextParser",
    "MarkdownParser",
    "PDFParser",
    "HTMLParser",
    "TreeBuilder",
    "BuildingTree",
    "VLMProcessor",
    "CLASS_PROCESSABLE",
    "CLASS_UNSUPPORTED",
    "ClassifiedFile",
    "DirectoryScanResult",
    "scan_directory",
    "ParseMode",
    "ParseModeInput",
]
