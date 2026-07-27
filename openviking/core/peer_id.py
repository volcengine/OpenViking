"""Helpers for peer identity fields."""

from __future__ import annotations

import base64
import re
from typing import Optional

from openviking.core.identifiers import normalize_identifier_part

_LEGACY_EXTERNAL_PEER_DISALLOWED = re.compile(r"[^a-zA-Z0-9_.@-]+")
_URLSAFE_BASE64 = re.compile(r"[A-Za-z0-9_-]+")


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


def legacy_external_peer_id_alias(peer_id: Optional[str]) -> Optional[str]:
    """Recover the older lossy alias for one encoded external peer id.

    Mixed-script external identities are now encoded in full as ``ext-<base64>``.
    Older ingest versions stripped their non-ASCII characters instead. Returning
    that former value lets read paths include durable history written before the
    collision-safe encoding was introduced, without sending new writes there.
    """
    normalized = safe_peer_id(peer_id)
    if not normalized or not normalized.startswith("ext-"):
        return None

    encoded = normalized.removeprefix("ext-")
    if not encoded or not _URLSAFE_BASE64.fullmatch(encoded):
        return None
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        raw = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None

    round_trip = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    if round_trip != encoded or raw.isascii():
        return None

    legacy = _LEGACY_EXTERNAL_PEER_DISALLOWED.sub("-", raw.strip())
    legacy = re.sub(r"-{2,}", "-", legacy).strip("-.")
    alias = safe_peer_id(legacy)
    return alias if alias and alias not in {normalized, "__self"} else None


def peer_id_aliases(peer_id: Optional[str]) -> list[str]:
    """Return the canonical peer id followed by any read-only legacy alias."""
    canonical = safe_peer_id(peer_id)
    if not canonical:
        return []
    legacy = legacy_external_peer_id_alias(canonical)
    return [canonical, legacy] if legacy else [canonical]
