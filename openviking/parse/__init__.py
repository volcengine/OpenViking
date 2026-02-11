# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Document parsers for various formats."""

from openviking.parse.base import NodeType, ParseResult, ResourceNode, create_parse_result
from openviking.parse.converter import DocumentConverter
from openviking.parse.custom import CallbackParserWrapper, CustomParserProtocol, CustomParserWrapper
from openviking.parse.directory_scan import (
    CLASS_PROCESSABLE,
    CLASS_UNSUPPORTED,
    ClassifiedFile,
    DirectoryScanResult,
    scan_directory,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.code import CodeRepositoryParser
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.parsers.text import TextParser
from openviking.parse.registry import ParserRegistry, get_registry, parse
from openviking.parse.tree_builder import TreeBuilder
from openviking.parse.vlm import VLMProcessor

__all__ = [
    # Base classes and helpers
    "BaseParser",
    "ResourceNode",
    "NodeType",
    "ParseResult",
    "create_parse_result",
    # Document parsers (core)
    "TextParser",
    "MarkdownParser",
    "PDFParser",
    "HTMLParser",
    "CodeRepositoryParser",
    "DocumentConverter",
    # Custom parser support
    "CustomParserProtocol",
    "CustomParserWrapper",
    "CallbackParserWrapper",
    # Registry
    "ParserRegistry",
    "get_registry",
    "parse",
    # Tree builder
    "TreeBuilder",
    "BuildingTree",
    # VLM
    "VLMProcessor",
    # Directory scan (phase-one validation)
    "CLASS_PROCESSABLE",
    "CLASS_UNSUPPORTED",
    "ClassifiedFile",
    "DirectoryScanResult",
    "scan_directory",
]
