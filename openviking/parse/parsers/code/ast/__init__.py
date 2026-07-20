# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public API for AST-based code skeleton extraction."""

from typing import Optional

from openviking.parse.parsers.code.ast.aider_repomap import (
    extract_query_skeleton,
    extract_repromap_skeleton,
)
from openviking.parse.parsers.code.ast.extractor import get_extractor, get_process_extractor
from openviking_cli.utils.config import get_openviking_config


def _configured_skeleton_provider() -> str:
    try:
        return getattr(get_openviking_config().code, "code_skeleton_provider", "ov_ast")
    except Exception:
        return "ov_ast"


def extract_skeleton(file_name: str, content: str, verbose: bool = False) -> Optional[str]:
    """Extract a skeleton from source code.

    Supports Python, JS/TS, Java, C/C++, Rust, Go via tree-sitter.
    Returns None for unsupported languages or on extraction failure,
    signalling the caller to fall back to LLM.

    Args:
        file_name: File name with extension (used for language detection).
        content: Source code content.
        verbose: If True, include full docstrings (for ast_llm / LLM input).
                 If False, only first line of each docstring (for ast / embedding).

    Returns:
        Plain-text skeleton string, or None if unsupported / failed.
    """
    provider = _configured_skeleton_provider()
    if provider == "aider_repomap":
        return extract_repromap_skeleton(file_name, content, verbose=verbose)
    if provider in ("repomap_query", "aider_query"):
        return extract_query_skeleton(file_name, content, verbose=verbose)
    if provider == "process":
        return get_process_extractor().extract_skeleton(file_name, content, verbose=verbose)

    return get_extractor().extract_skeleton(file_name, content, verbose=verbose)


__all__ = ["extract_skeleton"]
