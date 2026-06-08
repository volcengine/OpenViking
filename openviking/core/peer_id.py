"""Helpers for peer identity fields."""

from __future__ import annotations

from typing import Optional


def normalize_peer_id(
    peer_id: Optional[str],
) -> Optional[str]:
    """Normalize a peer_id value."""
    if peer_id == "":
        peer_id = None

    normalized = peer_id
    if normalized and ("/" in normalized or "\\" in normalized):
        raise ValueError("peer_id must not contain path separators")
    return normalized


def safe_peer_id(peer_id: Optional[str]) -> Optional[str]:
    """Return a usable peer_id, or None for empty/path-like values."""
    if not peer_id:
        return None
    if "/" in peer_id or "\\" in peer_id:
        return None
    return peer_id
