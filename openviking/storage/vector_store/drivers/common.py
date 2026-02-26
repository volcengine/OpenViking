# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for vector backend drivers."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlparse


def parse_url(url: str) -> tuple[str, int]:
    """Parse backend URL to host/port pair."""
    normalized = url
    if not normalized.startswith(("http://", "https://")):
        normalized = f"http://{normalized}"

    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5000
    return host, port


def normalize_collection_names(raw_collections: Iterable[Any]) -> list[str]:
    """Normalize collection listing results to plain collection-name strings."""
    names: list[str] = []
    for item in raw_collections:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("CollectionName") or item.get("collection_name") or item.get("name")
            if isinstance(name, str):
                names.append(name)
    return names
