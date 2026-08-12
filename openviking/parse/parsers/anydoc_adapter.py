"""Normalize firecrawl-anydoc Python binding attribute names."""

from __future__ import annotations

from typing import Any, Optional


def attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if obj is None:
            break
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def asset_id(source: Any) -> Optional[int]:
    raw = attr(source, "asset_id", "assetId")
    if raw is None:
        return None
    if hasattr(raw, "id"):  # AssetId wrapper
        return int(getattr(raw, "id", raw))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
