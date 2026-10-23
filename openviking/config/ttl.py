# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Merge policy sources per node, then select root > type > global > disabled.

Cluster already includes the startup/runtime merge. Account.global only replaces
Cluster.global; it never overrides a more specific Cluster type or root policy.
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
    cluster_ttl: Optional[TTLConfig], account_ttl: Optional[TTLConfig], uri: str
) -> EffectiveTTL:
    """Resolve one URI using the already-loaded configuration pair; no I/O."""
    kind = classify_ttl_uri(uri)
    if kind is None:
        return EffectiveTTL.off(None)
    target = uri_parts(uri)
    roots = {}
    for config in (cluster_ttl, account_ttl):
        if config is not None:
            roots.update(config.roots or {})
    root_policy, root_depth = None, -1
    for root, policy in roots.items():
        parts = uri_parts(root)
        if len(parts) > root_depth and target[: len(parts)] == parts:
            root_policy, root_depth = policy, len(parts)
    if root_policy is not None:
        return (
            EffectiveTTL.off(kind)
            if root_policy == "disabled"
            else EffectiveTTL(kind, True, root_policy)
        )
    for node in (kind.value, "global_"):
        value = getattr(account_ttl, node, None)
        if value is None:
            value = getattr(cluster_ttl, node, None)
        if value is not None:
            return (
                EffectiveTTL.off(kind) if value == "disabled" else EffectiveTTL(kind, True, value)
            )
    return EffectiveTTL.off(kind)
