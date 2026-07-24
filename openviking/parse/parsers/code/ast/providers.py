# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Provider orchestration for code skeleton extraction."""

import re
from typing import Optional

from openviking.parse.parsers.code.ast.aider_repomap import (
    extract_query_skeleton,
    extract_repromap_skeleton,
)
from openviking.parse.parsers.code.ast.extractor import get_extractor, get_process_extractor
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(class|def|func|function|interface|struct|enum|trait)\s+\w", re.M),
    re.compile(r"^\s*\+\s+\w", re.M),
    re.compile(r"^\s*-\s+L\d+:\s+\w", re.M),
)
_IMPORT_ONLY_PREFIXES = ("#", "imports:", "module:", "language:")
_PROVIDER_ALIASES = {
    "query": "aider_repomap",
    "aider_query": "repomap_query",
}
_SUPPORTED_PROVIDERS = {"ov_ast", "process", "aider_repomap", "repomap_query"}


def _configured_provider() -> str:
    try:
        config = get_openviking_config()
        provider = getattr(config.code, "code_skeleton_provider", "ov_ast")
        return _PROVIDER_ALIASES.get(provider, provider)
    except Exception:
        return "ov_ast"


def _has_symbol(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SYMBOL_PATTERNS)


def is_skeleton_useful(text: Optional[str]) -> bool:
    """Return True when a skeleton has enough structure to index directly."""

    if not text or not text.strip():
        return False

    meaningful_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(_IMPORT_ONLY_PREFIXES)
    ]
    if len(meaningful_lines) < 2:
        return False

    return _has_symbol(text)


def _extract_primary(provider: str, file_name: str, content: str, verbose: bool) -> Optional[str]:
    if provider == "ov_ast":
        return get_extractor().extract_skeleton(file_name, content, verbose=verbose)
    if provider == "process":
        return get_process_extractor().extract_skeleton(file_name, content, verbose=verbose)
    if provider == "aider_repomap":
        return extract_repromap_skeleton(file_name, content, verbose=verbose)
    if provider == "repomap_query":
        return extract_query_skeleton(file_name, content, verbose=verbose)
    raise ValueError(
        f"Unsupported code_skeleton_provider '{provider}'. "
        f"Supported providers: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
    )


def _empty_provider_result(provider: str, file_name: str, reason: str) -> str:
    return f"# {file_name} [{provider}]\n\nNo extractable code skeleton ({reason})."


def extract_skeleton_with_provider(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> str:
    """Extract skeleton using the configured provider only.

    Non-LLM code summary modes must stay deterministic: this function never
    switches to another skeleton provider and never returns None.
    """

    provider = _configured_provider()
    text = _extract_primary(provider, file_name, content, verbose)
    if text and text.strip():
        if not is_skeleton_useful(text):
            logger.info(
                "Code skeleton provider '%s' produced low-quality skeleton without fallback: %s",
                provider,
                file_name,
            )
        return text

    if not content.strip():
        reason = "empty file"
    else:
        reason = "unsupported language or no definitions found"
    logger.info(
        "Code skeleton provider '%s' produced no skeleton without fallback: %s",
        provider,
        file_name,
    )
    return _empty_provider_result(provider, file_name, reason)
