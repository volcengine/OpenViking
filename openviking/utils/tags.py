# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Utilities for explicit search tags."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Iterable

from openviking_cli.exceptions import InvalidArgumentError

logger = logging.getLogger(__name__)


def normalize_search_tag(tag: str) -> str:
    """Normalize a search tag, rejecting only literal commas."""
    value = str(tag).strip().lower()
    if "," in value:
        raise InvalidArgumentError(f"invalid search tag '{tag}': must not contain ','")
    return value


def normalize_search_tags(
    tags: Iterable[str] | None,
    *,
    discard_invalid: bool = False,
) -> list[str]:
    """Normalize tags in stable order, keeping the last value for each k=v key.

    Plain tags are deduplicated separately from k=v tags. ``discard_invalid``
    discards tags containing commas instead of raising an error.
    """
    if not tags:
        return []

    values_by_key: OrderedDict[tuple[str, bool], str] = OrderedDict()
    invalid_tags: list[str] = []
    for item in tags:
        if item is None:
            continue
        try:
            value = normalize_search_tag(item)
        except InvalidArgumentError:
            if discard_invalid:
                invalid_tags.append(str(item))
                continue
            raise
        key, separator, _ = value.partition("=")
        values_by_key[(key, bool(separator))] = value
    if invalid_tags:
        logger.warning(
            "Discarded invalid search tags: %s",
            invalid_tags,
            extra={
                "invalid_tags": invalid_tags,
                "invalid_tag_count": len(invalid_tags),
            },
        )
    return list(values_by_key.values())


def build_search_tags_filter(tags: Iterable[str] | None) -> dict[str, Any] | None:
    """Build a metadata filter that requires every explicit search tag."""
    normalized_tags = normalize_search_tags(tags)
    if not normalized_tags:
        return None

    tag_filters = [
        {
            "op": "must",
            "field": "search_tags",
            "conds": [tag],
        }
        for tag in normalized_tags
    ]
    if len(tag_filters) == 1:
        return tag_filters[0]
    return {"op": "and", "conds": tag_filters}


def merge_search_tags(existing: Iterable[str] | None, incoming: Iterable[str] | None) -> list[str]:
    """Merge tags, replacing existing k=v values while preserving plain tags."""
    return normalize_search_tags([*(existing or []), *(incoming or [])], discard_invalid=True)
