# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared vector scope filter composition."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openviking.storage.expr import And, Eq, FilterExpr, In, Or, PathScope, RawDSL

FilterInput = Optional[FilterExpr | Dict[str, Any]]


def merge_filters(*filters: FilterInput) -> Optional[FilterExpr]:
    """Combine non-empty filters without changing single-filter expressions."""
    non_empty: list[FilterExpr] = []
    for item in filters:
        if not item:
            continue
        if isinstance(item, dict):
            item = RawDSL(item)
        if (
            isinstance(item, RawDSL)
            and item.payload.get("op") == "and"
            and not item.payload.get("conds")
        ):
            continue
        non_empty.append(item)
    if not non_empty:
        return None
    if len(non_empty) == 1:
        return non_empty[0]
    return And(non_empty)


def build_scope_filter(
    *,
    context_type: Optional[str] = None,
    base_filter: FilterInput = None,
    target_directories: Optional[List[str]] = None,
    target_depth: int | None = -1,
    wrap_single_target: bool = False,
    extra_filter: FilterInput = None,
    level: Optional[list[int]] = None,
) -> Optional[FilterExpr]:
    """Build a vector query scope while keeping caller-specific path depth."""
    filters: list[FilterExpr] = []
    if context_type:
        filters.append(Eq("context_type", context_type))
    if base_filter:
        if isinstance(base_filter, dict):
            base_filter = RawDSL(base_filter)
        filters.append(base_filter)

    targets = [target for target in target_directories or [] if target]
    target_scopes = [PathScope("uri", target, depth=target_depth) for target in targets]
    if target_scopes:
        filters.append(
            Or(target_scopes) if wrap_single_target or len(target_scopes) > 1 else target_scopes[0]
        )

    if extra_filter:
        if isinstance(extra_filter, dict):
            extra_filter = RawDSL(extra_filter)
        filters.append(extra_filter)
    if level:
        filters.append(In("level", level))
    return merge_filters(*filters)
