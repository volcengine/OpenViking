# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared grammar for reserved ``MEMORY_FIELDS`` comments."""

from __future__ import annotations

import re
from typing import Match, Optional

MEMORY_FIELDS_COMMENT_RE = re.compile(r"<!--\s*MEMORY_FIELDS\b(?P<fields>[\s\S]*?)\s*-->")


def find_memory_fields_trailer(content: str) -> Optional[Match[str]]:
    """Return the reserved comment when it is the final content element."""
    trailer = None
    for match in MEMORY_FIELDS_COMMENT_RE.finditer(content):
        if not content[match.end() :].strip():
            trailer = match
    return trailer


def strip_memory_fields_trailer(content: str) -> str:
    """Remove all trailing reserved comments and their whitespace separators."""
    visible = content
    while match := find_memory_fields_trailer(visible):
        visible = visible[: match.start()].rstrip()
    return visible
