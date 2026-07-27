# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Routing for tags-query, process, and LLM-fallback code summaries."""

import re
from dataclasses import dataclass
from typing import Optional

from openviking.parse.parsers.code.ast.aider_repomap import (
    extract_repromap_skeleton,
    has_tag_query,
)
from openviking.parse.parsers.code.ast.extractor import get_process_extractor
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(class|def|func|function|interface|struct|enum|trait)\s+\w", re.M),
    re.compile(r"^\s*\+\s+\w", re.M),
    re.compile(r"^\s*-\s+L\d+:\s+\w", re.M),
    re.compile(r"^\s*[\w:<>,*&~]+\s+\w+\s*\([^)]*\)\s*(?:const)?\s*[;{]?", re.M),
)
_IMPORT_ONLY_PREFIXES = ("#", "imports:", "module:", "language:")


@dataclass(frozen=True)
class SkeletonExtractionResult:
    text: Optional[str]
    provider: str
    should_fallback_to_llm: bool
    reason: str


def is_skeleton_useful(text: Optional[str]) -> bool:
    """Return whether a skeleton contains meaningful symbol structure."""

    if not text or not text.strip():
        return False
    meaningful_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(_IMPORT_ONLY_PREFIXES)
    ]
    return len(meaningful_lines) >= 1 and any(pattern.search(text) for pattern in _SYMBOL_PATTERNS)


def extract_skeleton_with_routing(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> SkeletonExtractionResult:
    """Extract a skeleton, signalling when the caller must use LLM fallback."""

    reasons: list[str] = []
    if has_tag_query(file_name):
        text = extract_repromap_skeleton(file_name, content, verbose=verbose)
        if is_skeleton_useful(text):
            return SkeletonExtractionResult(text, "aider_repomap", False, "maintained tags query")
        reasons.append("tags query produced no useful skeleton")
    else:
        reasons.append("no maintained tags query")

    text = get_process_extractor().extract_skeleton(file_name, content, verbose=verbose)
    if is_skeleton_useful(text):
        return SkeletonExtractionResult(text, "process", False, "process extraction succeeded")
    reasons.append("process produced no useful skeleton")

    reason = "; ".join(reasons)
    logger.info("Code skeleton requires LLM fallback for '%s': %s", file_name, reason)
    return SkeletonExtractionResult(None, "llm", True, reason)


def extract_skeleton_result(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> SkeletonExtractionResult:
    return extract_skeleton_with_routing(file_name, content, verbose=verbose)


def extract_skeleton(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> Optional[str]:
    return extract_skeleton_result(file_name, content, verbose=verbose).text
