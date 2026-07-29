# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Code skeleton extraction via tree-sitter-language-pack.process()."""

from pathlib import Path
from typing import Optional

from tree_sitter_language_pack import (
    ProcessConfig,
    SupportedLanguage,
    detect_language_from_path,
    process,
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
    "latex",
    "mermaid",
    "dot",
    "http",
    "hurl",
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
    ".tex",
    ".latex",
    ".mmd",
    ".mermaid",
    ".dot",
    ".http",
    ".hurl",
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


def _display_language(lang: str) -> str:
    return _DISPLAY.get(lang, lang.replace("_", " ").title())


def _detect_process_language(file_name: str) -> Optional[str]:
    path = Path(file_name)
    if path.suffix.lower() in _PROCESS_SUFFIX_DENYLIST:
        return None

    try:
        lang = detect_language_from_path(str(path))
    except Exception as exc:
        logger.warning("process language detection failed for '%s': %s", file_name, exc)
        return None

    if (
        lang is None
        or lang not in _SUPPORTED_PROCESS_LANGUAGES
        or lang in _PROCESS_LANGUAGE_DENYLIST
    ):
        return None
    return lang


def _extract_process_skeleton(file_name: str, content: str, lang: str) -> str:
    result = process(
        content,
        ProcessConfig(language=lang, structure=True, imports=True),
    )

    lines: list[str] = [f"# {file_name} [{_display_language(lang)}]"]

    imports = [item.source.strip() for item in result.imports if item.source]
    if imports:
        lines.append(f"imports: {', '.join(imports)}")
    lines.append("")

    for node in result.structure:
        kind = str(node.kind).lower()
        if kind in _CLASS_KINDS:
            lines.append(f"class {node.name or ''}")
            for child in node.children:
                if str(child.kind).lower() in _FUNC_KINDS and child.name:
                    lines.append(f"  + {child.name}()")
            lines.append("")
        elif kind in _FUNC_KINDS and node.name:
            lines.append(f"def {node.name}()")

    return "\n".join(lines).strip()


def supports_process_skeleton(file_name: str) -> bool:
    return _detect_process_language(file_name) is not None


def extract_process_skeleton(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> Optional[str]:
    del verbose
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
