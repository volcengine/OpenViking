"""Helpers for peer identity fields."""

from __future__ import annotations

from typing import Optional


def normalize_peer_id(
    peer_id: Optional[str],
    agent_id: Optional[str] = None,
    role_id: Optional[str] = None,
) -> Optional[str]:
    """Normalize legacy message identity fields into peer_id."""
    if peer_id == "":
        peer_id = None
    if agent_id == "":
        agent_id = None
    if role_id == "":
        role_id = None

    if peer_id and agent_id and peer_id != agent_id:
        raise ValueError("peer_id and agent_id must match when both are provided")
    if peer_id and role_id and peer_id != role_id:
        raise ValueError("peer_id and role_id must match when both are provided")
    if agent_id and role_id and agent_id != role_id:
        raise ValueError("agent_id and role_id must match when both are provided")

    normalized = peer_id or agent_id or role_id
    if normalized and ("/" in normalized or "\\" in normalized):
        raise ValueError("peer_id must not contain path separators")
    return normalized
