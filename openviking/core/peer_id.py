"""Helpers for peer identity fields."""

from __future__ import annotations

from typing import Optional

from openviking.core.identifiers import normalize_identifier_part


def normalize_peer_id(
    peer_id: Optional[str],
) -> Optional[str]:
    """Normalize a peer_id value."""
    try:
        return normalize_identifier_part(peer_id, "peer_id")
    except ValueError as exc:
        raise ValueError(f"Invalid peer_id: {exc}") from exc


def safe_peer_id(peer_id: Optional[str]) -> Optional[str]:
    """Return a usable peer_id, or None for empty/path-like values."""
    try:
        return normalize_peer_id(peer_id)
    except ValueError:
        return None
