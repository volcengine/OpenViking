# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Routing for tags-query, process, and LLM-fallback code summaries."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openviking.parse.parsers.code.ast.aider_repomap import (
    _extract_with_grep_ast,
    has_tag_query,
)
from openviking.parse.parsers.code.ast.process_engine import (
    extract_process_skeleton,
    supports_process_skeleton,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_IMPORT_ONLY_PREFIXES = ("#", "imports:", "module:", "language:")


def supports_code_skeleton(file_name: str) -> bool:
    """Return whether tags or process extraction recognizes the file."""

    return has_tag_query(file_name) or supports_process_skeleton(file_name)


@dataclass(frozen=True)
class SkeletonExtractionResult:
    text: Optional[str]
    provider: str
    should_fallback_to_llm: bool
    reason: str


def is_skeleton_useful(text: Optional[str]) -> bool:
    """Return whether a skeleton contains extractor-produced structure."""

    if not text or not text.strip():
        return False
    return any(
        line.strip() and not line.strip().startswith(_IMPORT_ONLY_PREFIXES)
        for line in text.splitlines()
    )


def extract_skeleton_result(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> SkeletonExtractionResult:
    """Extract a skeleton, signalling when the caller must use LLM fallback."""

    reasons: list[str] = []
    if has_tag_query(file_name):
        text = None
        if content:
            rel_name = Path(file_name).name or "source.txt"
            text = _extract_with_grep_ast(file_name, rel_name, content, verbose)
        if is_skeleton_useful(text):
            return SkeletonExtractionResult(text, "aider_repomap", False, "maintained tags query")
        reason = "tags query produced no useful skeleton"
        logger.debug("Code skeleton requires LLM fallback for '%s': %s", file_name, reason)
        return SkeletonExtractionResult(None, "llm", True, reason)
    else:
        reasons.append("no maintained tags query")

    text = extract_process_skeleton(file_name, content, verbose=verbose)
    if is_skeleton_useful(text):
        return SkeletonExtractionResult(text, "process", False, "process extraction succeeded")
    reasons.append("process produced no useful skeleton")

    reason = "; ".join(reasons)
    logger.debug("Code skeleton requires LLM fallback for '%s': %s", file_name, reason)
    return SkeletonExtractionResult(None, "llm", True, reason)
