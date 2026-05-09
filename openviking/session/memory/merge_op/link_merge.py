# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Link merge and dedup logic for MEMORY_FIELDS links field.

Dedup key: from_uri + to_uri + t_field + match_text
Merge rules:
- Weight conflict: take max
- link_type and description: latest write wins
- t_line_ranges: union on merge
"""

from typing import Any, Dict, List, Optional, Set, Tuple


def _dedup_key(link: Dict[str, Any]) -> str:
    """Compute dedup key for a link."""
    return f"{link.get('from_uri', '')}|{link.get('to_uri', '')}|{link.get('t_field', '')}|{link.get('match_text', '')}"


def _parse_ranges(ranges_str: Optional[str]) -> List[Tuple[int, int]]:
    """Parse a ranges string like '3-5,8-10' into list of (start, end) tuples."""
    if not ranges_str:
        return []
    result = []
    for part in ranges_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                result.append((int(start.strip()), int(end.strip())))
            except (ValueError, TypeError):
                continue
    return result


def _format_ranges(ranges: List[Tuple[int, int]]) -> str:
    """Format list of (start, end) tuples into ranges string like '3-5,8-10'."""
    if not ranges:
        return ""
    # Sort and merge overlapping/adjacent ranges
    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return ",".join(f"{s}-{e}" for s, e in merged)


def merge_links(existing_links: List[Dict], new_links: List[Dict]) -> List[Dict]:
    """
    Merge link lists with dedup and conflict resolution.

    Dedup key: from_uri + to_uri + t_field + match_text
    Weight conflict: take max
    link_type and description: latest write wins
    t_line_ranges: union on merge
    """
    link_map: Dict[str, Dict[str, Any]] = {}

    # Process existing links first
    for link in existing_links:
        key = _dedup_key(link)
        link_map[key] = dict(link)

    # Process new links (override existing on conflict)
    for link in new_links:
        key = _dedup_key(link)
        if key in link_map:
            existing = link_map[key]
            # Weight: take max
            existing["weight"] = max(existing.get("weight", 1.0), link.get("weight", 1.0))
            # link_type and description: latest write wins
            if "link_type" in link:
                existing["link_type"] = link["link_type"]
            if "description" in link:
                existing["description"] = link["description"]
            # t_line_ranges: union
            existing_ranges = _parse_ranges(existing.get("t_line_ranges"))
            new_ranges = _parse_ranges(link.get("t_line_ranges"))
            union_ranges = list(set(existing_ranges) | set(new_ranges))
            if union_ranges:
                existing["t_line_ranges"] = _format_ranges(union_ranges)
            # created_at: keep the original
        else:
            link_map[key] = dict(link)

    return list(link_map.values())
