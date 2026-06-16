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


def peer_id_from_legacy_agent_uri(agent_uri: Optional[str]) -> Optional[str]:
    """Resolve a legacy agent URI or ID into a peer_id."""
    if not agent_uri:
        return None
    value = agent_uri.strip()
    if not value:
        return None
    if value.startswith("viking://agent/"):
        parts = [part for part in value[len("viking://agent/") :].strip("/").split("/") if part]
        value = parts[0] if parts else ""
    return normalize_peer_id(value)


def normalize_peer_selector(
    peer_id: Optional[str],
    *,
    agent_id: Optional[str] = None,
    agent_uri: Optional[str] = None,
) -> Optional[str]:
    """Normalize peer_id with legacy agent_id/agent_uri compatibility."""
    resolved_peer_id = normalize_peer_id(peer_id)
    legacy_agent_id = normalize_peer_id(agent_id)
    legacy_agent_uri_id = peer_id_from_legacy_agent_uri(agent_uri)
    if legacy_agent_id and legacy_agent_uri_id and legacy_agent_id != legacy_agent_uri_id:
        raise ValueError("legacy agent_id must match agent_uri")
    legacy_peer_id = legacy_agent_id or legacy_agent_uri_id
    if resolved_peer_id and legacy_peer_id:
        raise ValueError("peer_id cannot be used with legacy agent_id/agent_uri")
    return resolved_peer_id or legacy_peer_id


def safe_peer_id(peer_id: Optional[str]) -> Optional[str]:
    """Return a usable peer_id, or None for empty/path-like values."""
    try:
        return normalize_peer_id(peer_id)
    except ValueError:
        return None
