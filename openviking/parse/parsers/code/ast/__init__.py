# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Public API for AST-based code skeleton extraction."""

from typing import Optional

from openviking.parse.parsers.code.ast.extractor import get_extractor


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
    return get_extractor().extract_skeleton(file_name, content, verbose=verbose)


__all__ = ["extract_skeleton"]
