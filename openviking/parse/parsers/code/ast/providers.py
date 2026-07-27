# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Routing for tags-query, process, and LLM-fallback code summaries."""

import re
from dataclasses import dataclass
from typing import Optional

from openviking.parse.parsers.code.ast.aider_repomap import (
    extract_query_skeleton,
    extract_repromap_skeleton,
    has_tag_query,
)
from openviking.parse.parsers.code.ast.extractor import get_process_extractor
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(class|def|func|function|interface|struct|enum|trait)\s+\w", re.M),
    re.compile(r"^\s*\+\s+\w", re.M),
    re.compile(r"^\s*-\s+L\d+:\s+\w", re.M),
    re.compile(r"^\s*[\w:<>,*&~]+\s+\w+\s*\([^)]*\)\s*(?:const)?\s*[;{]?", re.M),
)
_IMPORT_ONLY_PREFIXES = ("#", "imports:", "module:", "language:")
_PROVIDER_ALIASES = {
    "query": "aider_repomap",
    "aider_query": "repomap_query",
}
_SUPPORTED_PROVIDERS = {
    "auto",
    "ov_ast",
    "process",
    "aider_repomap",
    "repomap_query",
}


@dataclass(frozen=True)
class SkeletonExtractionResult:
    text: Optional[str]
    provider: str
    should_fallback_to_llm: bool
    reason: str


def _configured_provider() -> str:
    try:
        provider = getattr(get_openviking_config().code, "code_skeleton_provider", "auto")
    except Exception:
        provider = "auto"
    provider = _PROVIDER_ALIASES.get(provider, provider)
    if provider == "ov_ast":
        logger.warning(
            "code_skeleton_provider='ov_ast' is deprecated; using automatic routing"
        )
        return "auto"
    return provider


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


def _result(text: Optional[str], provider: str, reason: str) -> SkeletonExtractionResult:
    useful = is_skeleton_useful(text)
    return SkeletonExtractionResult(
        text=text if useful else None,
        provider=provider,
        should_fallback_to_llm=not useful,
        reason=reason if useful else f"{reason}: empty or low-quality skeleton",
    )


def _extract_forced(
    provider: str,
    file_name: str,
    content: str,
    verbose: bool,
) -> SkeletonExtractionResult:
    if provider == "process":
        text = get_process_extractor().extract_skeleton(file_name, content, verbose=verbose)
    elif provider == "aider_repomap":
        text = extract_repromap_skeleton(file_name, content, verbose=verbose)
    elif provider == "repomap_query":
        text = extract_query_skeleton(file_name, content, verbose=verbose)
    else:
        raise ValueError(
            f"Unsupported code_skeleton_provider '{provider}'. "
            f"Supported providers: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
        )
    return _result(text, provider, f"configured provider '{provider}'")


def extract_skeleton_with_routing(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> SkeletonExtractionResult:
    """Extract a skeleton, signalling when the caller must use LLM fallback."""

    configured = _configured_provider()
    if configured != "auto":
        return _extract_forced(configured, file_name, content, verbose)

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
