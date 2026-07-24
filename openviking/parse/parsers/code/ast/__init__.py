# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public API for AST-based code skeleton extraction."""

from openviking.parse.parsers.code.ast.providers import extract_skeleton_with_provider


def extract_skeleton(file_name: str, content: str, verbose: bool = False) -> str:
    """Extract a skeleton from source code.

    Supports Python, JS/TS, Java, C/C++, Rust, Go via tree-sitter.
    Returns deterministic provider output for unsupported languages or extraction
    failures; callers should not use this API to trigger LLM summarization.

    Args:
        file_name: File name with extension (used for language detection).
        content: Source code content.
        verbose: If True, include full docstrings (for ast_llm / LLM input).
                 If False, only first line of each docstring (for ast / embedding).

    Returns:
        Plain-text skeleton string.
    """
    return extract_skeleton_with_provider(file_name, content, verbose=verbose)


__all__ = ["extract_skeleton"]
