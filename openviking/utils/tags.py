# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Utilities for explicit k=v search tags."""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

from openviking_cli.exceptions import InvalidArgumentError


def normalize_search_tag(tag: str) -> str:
    """Validate and normalize a single k=v search tag."""
    value = str(tag).strip().lower()
    if not value:
        raise InvalidArgumentError("search tag must be a non-empty k=v string")
    if value.count("=") != 1:
        raise InvalidArgumentError(f"invalid search tag '{tag}': expected strict k=v format")

    key, raw_value = value.split("=", 1)
    if not key or not raw_value:
        raise InvalidArgumentError(
            f"invalid search tag '{tag}': key and value must both be non-empty"
        )
    return f"{key}={raw_value}"


def normalize_search_tags(tags: Iterable[str] | None) -> list[str]:
    """Normalize explicit search tags while preserving stable order."""
    if not tags:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in tags:
        if item is None:
            continue
        value = normalize_search_tag(item)
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def merge_search_tags(existing: Iterable[str] | None, incoming: Iterable[str] | None) -> list[str]:
    """Merge normalized search tags by key, replacing old values with incoming ones."""
    ordered: OrderedDict[str, str] = OrderedDict()

    for item in normalize_search_tags(existing):
        key, value = item.split("=", 1)
        ordered[key] = value

    for item in normalize_search_tags(incoming):
        key, value = item.split("=", 1)
        ordered[key] = value

    return [f"{key}={value}" for key, value in ordered.items()]
