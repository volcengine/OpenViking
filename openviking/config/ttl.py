# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve TTL per field: User > Account > global, then fall back to global TTL.

All of a user's Peers share peer_events. Unset fields inherit; disabled
stops fallback. Configuration sources are already loaded, so resolution has no I/O.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from openviking.core.namespace import uri_parts
from openviking_cli.utils.config.ttl_config import TTLConfig


class TTLIndexType(str, Enum):
    USER_EVENTS = "user_events"
    PEER_EVENTS = "peer_events"
    SESSIONS = "sessions"


TTL_ROOT_DEPTH = {
    TTLIndexType.USER_EVENTS: 4,
    TTLIndexType.PEER_EVENTS: 6,
    TTLIndexType.SESSIONS: 3,
}


@dataclass(frozen=True)
class EffectiveTTL:
    index_type: Optional[TTLIndexType]
    enabled: bool
    days: Optional[int]

    @classmethod
    def off(cls, index_type: Optional[TTLIndexType]) -> "EffectiveTTL":
        return cls(index_type=index_type, enabled=False, days=None)


def classify_ttl_uri(uri: str) -> Optional[TTLIndexType]:
    """Identify supported user/peer Events and Sessions, including descendants."""
    try:
        parts = uri_parts(uri)
    except ValueError:
        return None
    if len(parts) < 3 or parts[0] != "user":
        return None
    if len(parts) == 3 and parts[2] == "peers":
        return TTLIndexType.PEER_EVENTS  # Shared user configuration scope.
    # user/{uid}/peers/{peer_id}/memories/events[/...]
    if len(parts) >= 6 and parts[2] == "peers" and parts[4] == "memories" and parts[5] == "events":
        return TTLIndexType.PEER_EVENTS
    # user/{uid}/memories/events[/...]
    if len(parts) >= 4 and parts[2] == "memories" and parts[3] == "events":
        return TTLIndexType.USER_EVENTS
    # user/{uid}/sessions[/...]
    if parts[2] == "sessions":
        return TTLIndexType.SESSIONS
    return None


def resolve_effective_ttl(
    cluster_ttl: Optional[TTLConfig],
    account_ttl: Optional[TTLConfig],
    uri: str,
    user_ttl: Optional[TTLConfig] = None,
) -> EffectiveTTL:
    """Resolve one URI using the already-loaded configuration pair; no I/O."""
    kind = classify_ttl_uri(uri)
    if kind is None:
        return EffectiveTTL.off(None)
    for node in (kind.value, "global_"):
        for config in (user_ttl, account_ttl, cluster_ttl):
            value = getattr(config, node, None)
            if value is not None:
                return (
                    EffectiveTTL.off(kind)
                    if value == "disabled"
                    else EffectiveTTL(kind, True, value)
                )
    return EffectiveTTL.off(kind)


def validate_ttl_root(uri: str, *, allow_peer_scope: bool = False) -> None:
    """Validate a concrete scan root, or the shared Peer scope for policy reads."""
    from openviking.core.identifiers import validate_identifier_part

    kind = classify_ttl_uri(uri)
    parts = uri_parts(uri)
    peer_scope = allow_peer_scope and kind == TTLIndexType.PEER_EVENTS and len(parts) == 3
    if (
        kind is None
        or (not peer_scope and len(parts) != TTL_ROOT_DEPTH[kind])
        or uri != "viking://" + "/".join(parts)
        or validate_identifier_part(parts[1], "user_id")
        or (
            kind == TTLIndexType.PEER_EVENTS
            and not peer_scope
            and validate_identifier_part(parts[3], "peer_id")
        )
    ):
        raise ValueError("TTL target must be a canonical Events or Sessions root")
