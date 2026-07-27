# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-backed extractor compatibility helpers."""

from typing import Optional

from openviking.parse.parsers.code.ast.languages.process_engine import ProcessAutoExtractor

_process_extractor: Optional[ProcessAutoExtractor] = None


def get_process_extractor() -> ProcessAutoExtractor:
    global _process_extractor
    if _process_extractor is None:
        _process_extractor = ProcessAutoExtractor()
    return _process_extractor


def supports_code_skeleton(file_name: str) -> bool:
    """Return whether tags or process extraction recognizes the file."""

    from openviking.parse.parsers.code.ast.aider_repomap import has_tag_query

    return has_tag_query(file_name) or get_process_extractor().supports(file_name)
