"""Helpers for turning external peer identities into safe local identifiers."""

import base64

from openviking.core.peer_id import normalize_peer_id


def normalize_external_peer_id(peer_id: object | None) -> str | None:
    """Return a stable, path-safe peer ID for a trusted external identity."""
    if peer_id is None:
        return None
    raw_peer_id = str(peer_id).strip()
    if not raw_peer_id or "/" in raw_peer_id or "\\" in raw_peer_id:
        return None
    try:
        return normalize_peer_id(raw_peer_id)
    except ValueError:
        encoded = base64.urlsafe_b64encode(raw_peer_id.encode("utf-8")).decode("ascii")
        return normalize_peer_id(f"ext-{encoded.rstrip('=')}")
