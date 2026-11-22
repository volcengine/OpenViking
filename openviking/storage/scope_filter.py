# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared vector scope filter composition."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openviking.core.namespace import uri_parts
from openviking.storage.expr import And, Eq, FilterExpr, In, Or, PathScope, RawDSL

FilterInput = Optional[FilterExpr | Dict[str, Any]]


def _canonical_uri_parts(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or not value.startswith("viking://"):
        return None
    if "?" in value or "#" in value or "\\" in value:
        return None
    suffix = value[len("viking://") :].rstrip("/")
    if suffix and any(part in {"", ".", ".."} for part in suffix.split("/")):
        return None
    return tuple(uri_parts(value))


def infer_uri_scopes(filter: FilterInput) -> tuple[PathScope, ...] | None:
    """Find a conservative URI bound using only positive typed predicates.

    Every match must be inside the returned scopes. Raw DSL is opaque: an AND
    can still use another typed condition, but every OR branch needs a bound.
    The original predicates, including finite depths, remain in the request.
    """
    if isinstance(filter, PathScope):
        if (
            filter.field == "uri"
            and type(filter.depth) is int
            and filter.depth >= -1
            and _canonical_uri_parts(filter.path) is not None
        ):
            return (filter,)
    elif isinstance(filter, Eq):
        if filter.field == "uri" and _canonical_uri_parts(filter.value) is not None:
            return (PathScope("uri", filter.value, depth=0),)
    elif isinstance(filter, And):
        for child in filter.conds:
            scopes = infer_uri_scopes(child)
            if scopes is not None:
                return scopes
    elif isinstance(filter, Or) and filter.conds:
        union: list[PathScope] = []
        for child in filter.conds:
            scopes = infer_uri_scopes(child)
            if scopes is None:
                return None
            union.extend(scopes)
        return tuple(dict.fromkeys(union))
    return None


def specialize_visibility_filter(
    tenant_filter: FilterExpr | None, scopes: tuple[PathScope, ...] | None
) -> FilterExpr | None:
    """Simplify generated visibility paths under a proven request URI bound.

    Account and ACL predicates are opaque. Truth values exist only during
    rewriting; a false result falls back rather than becoming an empty filter.
    """
    if tenant_filter is None or not scopes:
        return tenant_filter
    targets = [_canonical_uri_parts(scope.path) for scope in scopes]
    if any(target is None for target in targets):
        return tenant_filter

    def within(path: tuple[str, ...], root: tuple[str, ...]) -> bool:
        return path[: len(root)] == root

    def root_parts(expr: FilterExpr) -> tuple[str, ...] | None:
        if isinstance(expr, PathScope) and expr.field == "uri" and expr.depth == -1:
            return _canonical_uri_parts(expr.path)
        return None

    def rewrite(expr: FilterExpr) -> FilterExpr | bool:
        root = root_parts(expr)
        if root is not None:
            if all(within(target, root) for target in targets):
                return True
            if all(not within(target, root) and not within(root, target) for target in targets):
                return False
            return expr
        if not isinstance(expr, (And, Or)) or not expr.conds:
            return expr
        if isinstance(expr, Or):
            roots = [root_parts(child) for child in expr.conds]
            if all(root is not None for root in roots) and all(
                any(within(target, root) for root in roots) for target in targets
            ):
                return True
        children = [rewrite(child) for child in expr.conds]
        if isinstance(expr, And):
            if any(child is False for child in children):
                return False
            remaining = [child for child in children if child is not True]
            identity = True
        else:
            if any(child is True for child in children):
                return True
            remaining = [child for child in children if child is not False]
            identity = False
        if not remaining:
            return identity
        if len(remaining) == len(expr.conds) and all(
            child is original for child, original in zip(remaining, expr.conds, strict=True)
        ):
            return expr
        return remaining[0] if len(remaining) == 1 else type(expr)(remaining)

    result = rewrite(tenant_filter)
    if result is False:
        return tenant_filter
    return None if result is True else result


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
