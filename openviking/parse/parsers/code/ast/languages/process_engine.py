# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ProcessExtractor: simplified skeleton via tree-sitter-language-pack.process().

Provider option for code.code_skeleton_provider="process": intentionally drops
params / return types / docstrings / bases / module docstring. Only
class/function/method names + import source lines + 1-based line spans survive.
"""

from pathlib import Path
from typing import Optional

from tree_sitter_language_pack import (
    ProcessConfig,
    SupportedLanguage,
    detect_language_from_path,
    process,
)

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import (
    ClassSkeleton,
    CodeSkeleton,
    FunctionSig,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_CLASS_KINDS = {"class", "struct", "interface", "enum", "trait", "impl", "namespace"}
_FUNC_KINDS = {"function", "method"}

_DISPLAY = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "java": "Java",
    "c": "C",
    "cpp": "C/C++",
    "rust": "Rust",
    "go": "Go",
    "csharp": "C#",
    "php": "PHP",
    "lua": "Lua",
    "ruby": "Ruby",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "scala": "Scala",
    "terraform": "Terraform",
    "proto": "Protocol Buffers",
    "protobuf": "Protocol Buffers",
    "graphql": "GraphQL",
    "prisma": "Prisma",
    "sql": "SQL",
}

_PROC_LANG = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "java": "java",
    "cpp": "cpp",
    "rust": "rust",
    "go": "go",
    "csharp": "csharp",
    "php": "php",
    "lua": "lua",
}

_SUPPORTED_PROCESS_LANGUAGES = set(getattr(SupportedLanguage, "__args__", ()) or ())

_PROCESS_LANGUAGE_DENYLIST = {
    "markdown",
    "asciidoc",
    "html",
    "xml",
    "json",
    "json5",
    "jsonnet",
    "yaml",
    "toml",
    "ini",
    "csv",
    "tsv",
    "properties",
    "gitignore",
    "dockerfile",
    "make",
    "css",
    "scss",
    "less",
    "sass",
}

_PROCESS_SUFFIX_DENYLIST = {
    ".md",
    ".markdown",
    ".mdown",
    ".mkd",
    ".txt",
    ".rst",
    ".adoc",
    ".asciidoc",
    ".org",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".xml",
    ".html",
    ".htm",
    ".csv",
    ".tsv",
    ".log",
    ".lock",
    ".env",
    ".css",
    ".scss",
    ".less",
    ".sass",
}


def _span(node) -> tuple[int, int]:
    """process() spans are 0-based; convert to 1-based inclusive."""
    sp = node.span
    return int(sp.start_line) + 1, int(sp.end_line) + 1


def _func_sig(node) -> FunctionSig:
    ls, le = _span(node)
    return FunctionSig(
        name=node.name or "",
        params="",
        return_type="",
        docstring="",
        line_start=ls,
        line_end=le,
    )


def _display_language(lang: str) -> str:
    return _DISPLAY.get(lang, lang.replace("_", " ").title())


def _effective_path_for_detection(file_name: str) -> Path:
    path = Path(file_name)
    if path.suffix.lower() == ".md" and path.parent.name:
        parent_path = Path(path.parent.name)
        if parent_path.suffix:
            return parent_path
    return path


def _detect_process_language(file_name: str) -> Optional[str]:
    path = _effective_path_for_detection(file_name)
    if path.suffix.lower() in _PROCESS_SUFFIX_DENYLIST:
        return None

    try:
        lang = detect_language_from_path(str(path))
    except Exception as exc:
        logger.warning("process language detection failed for '%s': %s", file_name, exc)
        return None

    if lang is None:
        return None
    if lang not in _SUPPORTED_PROCESS_LANGUAGES:
        return None
    if lang in _PROCESS_LANGUAGE_DENYLIST:
        return None
    return lang


def _extract_process_skeleton(file_name: str, content: str, lang: str) -> CodeSkeleton:
    result = process(
        content,
        ProcessConfig(language=lang, structure=True, imports=True),
    )

    classes: list[ClassSkeleton] = []
    functions: list[FunctionSig] = []

    for node in result.structure:
        kind = str(node.kind).lower()
        if kind in _CLASS_KINDS:
            methods = [
                _func_sig(child)
                for child in node.children
                if str(child.kind).lower() in _FUNC_KINDS and child.name
            ]
            cls_ls, cls_le = _span(node)
            classes.append(
                ClassSkeleton(
                    name=node.name or "",
                    bases=[],
                    docstring="",
                    methods=methods,
                    line_start=cls_ls,
                    line_end=cls_le,
                )
            )
        elif kind in _FUNC_KINDS and node.name:
            functions.append(_func_sig(node))

    imports = [im.source.strip() for im in result.imports if im.source]

    return CodeSkeleton(
        file_name=file_name,
        language=_display_language(lang),
        module_doc="",
        imports=imports,
        classes=classes,
        functions=functions,
    )


class ProcessExtractor(LanguageExtractor):
    def __init__(self, lang: str):
        self._proc_lang = _PROC_LANG[lang]
        self._display = _DISPLAY[lang]

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        skeleton = _extract_process_skeleton(file_name, content, self._proc_lang)
        skeleton.language = self._display
        return skeleton


class ProcessAutoExtractor:
    """Path-detected process() skeleton extractor for code_skeleton_provider='process'."""

    def supports(self, file_name: str) -> bool:
        return _detect_process_language(file_name) is not None

    def extract(self, file_name: str, content: str) -> Optional[CodeSkeleton]:
        lang = _detect_process_language(file_name)
        if lang is None:
            return None

        try:
            return _extract_process_skeleton(file_name, content, lang)
        except Exception as exc:
            logger.warning(
                "process extraction failed for '%s' (language: %s): %s",
                file_name,
                lang,
                exc,
            )
            return None

    def extract_skeleton(
        self, file_name: str, content: str, verbose: bool = False
    ) -> Optional[str]:
        skeleton = self.extract(file_name, content)
        if skeleton is None:
            return None
        return skeleton.to_text(verbose=verbose)
