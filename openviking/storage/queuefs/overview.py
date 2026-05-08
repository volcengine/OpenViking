# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for reading generated overview content."""

from __future__ import annotations

import re


def parse_overview_md(overview_content: str) -> dict[str, str]:
    """Parse ``.overview.md`` content into file-name to summary mappings."""
    summaries: dict[str, str] = {}
    if not overview_content or not overview_content.strip():
        return summaries

    current_file: str | None = None
    current_summary_lines: list[str] = []

    for line in overview_content.split("\n"):
        header_match = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if header_match:
            if current_file and current_summary_lines:
                summaries[current_file] = " ".join(current_summary_lines).strip()

            file_name = header_match.group(1).strip()
            parts = file_name.split()
            if len(parts) >= 2 and parts[0] == parts[1]:
                file_name = parts[0]

            current_file = file_name
            current_summary_lines = []
            continue

        numbered_match = re.match(r"^\[(\d+)\]\s+(.+?):\s*(.+)$", line)
        if numbered_match:
            if current_file and current_summary_lines:
                summaries[current_file] = " ".join(current_summary_lines).strip()
            current_file = numbered_match.group(2).strip()
            current_summary_lines = [numbered_match.group(3).strip()]
            continue
        bullet_match = re.match(r"^[-*]\s+(.+?):\s*(.+)$", line)
        if bullet_match:
            if current_file and current_summary_lines:
                summaries[current_file] = " ".join(current_summary_lines).strip()
            current_file = bullet_match.group(1).strip()
            current_summary_lines = [bullet_match.group(2).strip()]
            continue

        if current_file:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                current_summary_lines.append(stripped)

    if current_file and current_summary_lines:
        summaries[current_file] = " ".join(current_summary_lines).strip()

    return summaries
