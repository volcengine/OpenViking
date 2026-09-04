# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Utilities for explicit k=v search tags."""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import Any, Iterable

from openviking_cli.exceptions import InvalidArgumentError

logger = logging.getLogger(__name__)

# Explicit search tags are meant to be small, enumerable business dimensions
# (env, team, source, ...). Constrain both sides of ``k=v`` so tags stay stable
# identifiers rather than free-form text: bounded length and a predictable
# character set after lower-casing (no internal spaces, no ``=``).
MAX_TAG_KEY_LENGTH = 64
MAX_TAG_VALUE_LENGTH = 128
MAX_TAG_LENGTH = MAX_TAG_KEY_LENGTH + 1 + MAX_TAG_VALUE_LENGTH
_TAG_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def normalize_search_tag(tag: str) -> str:
    """Validate and normalize a single k=v search tag."""
    value = str(tag).strip().lower()
    if not value:
        raise InvalidArgumentError("search tag must be a non-empty k=v string")
    if len(value) > MAX_TAG_LENGTH:
        raise InvalidArgumentError(
            f"invalid search tag '{tag}': exceeds max length {MAX_TAG_LENGTH}"
        )
    if value.count("=") != 1:
        raise InvalidArgumentError(f"invalid search tag '{tag}': expected strict k=v format")

    key, raw_value = value.split("=", 1)
    if not key or not raw_value:
        raise InvalidArgumentError(
            f"invalid search tag '{tag}': key and value must both be non-empty"
        )
    if len(key) > MAX_TAG_KEY_LENGTH:
        raise InvalidArgumentError(
            f"invalid search tag '{tag}': key exceeds max length {MAX_TAG_KEY_LENGTH}"
        )
    if len(raw_value) > MAX_TAG_VALUE_LENGTH:
        raise InvalidArgumentError(
            f"invalid search tag '{tag}': value exceeds max length {MAX_TAG_VALUE_LENGTH}"
        )
    if not _TAG_TOKEN_RE.match(key) or not _TAG_TOKEN_RE.match(raw_value):
        raise InvalidArgumentError(
            f"invalid search tag '{tag}': key and value may only contain lowercase "
            "letters, digits, '_', '-', '.', and must start with a letter or digit"
        )
    return f"{key}={raw_value}"


def normalize_search_tags(
    tags: Iterable[str] | None,
    *,
    discard_invalid: bool = False,
) -> list[str]:
    """Normalize explicit search tags while preserving stable order."""
    if not tags:
        return []

    values_by_key: OrderedDict[str, str] = OrderedDict()
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
        key, raw_value = value.split("=", 1)
        values_by_key[key] = raw_value
    if invalid_tags:
        logger.warning(
            "Discarded invalid search tags: %s",
            invalid_tags,
            extra={
                "invalid_tags": invalid_tags,
                "invalid_tag_count": len(invalid_tags),
            },
        )
    return [f"{key}={value}" for key, value in values_by_key.items()]


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
    """Merge normalized search tags by key, replacing old values with incoming ones."""
    ordered: OrderedDict[str, str] = OrderedDict()

    for item in normalize_search_tags(existing, discard_invalid=True):
        key, value = item.split("=", 1)
        ordered[key] = value

    for item in normalize_search_tags(incoming, discard_invalid=True):
        key, value = item.split("=", 1)
        ordered[key] = value

    return [f"{key}={value}" for key, value in ordered.items()]
