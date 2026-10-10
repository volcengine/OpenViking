# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default-off TTL policies for event files and whole sessions.

Priority: events/sessions root > type default > library global > off.
A disabled node stops inheritance. Object deadlines use the current policy.
"""

from typing import Dict, Literal, Optional

from pydantic import BaseModel, StrictInt, model_validator

from .runtime_field import RuntimeField

# The supported TTL scopes. Deliberately closed: TTL never applies to any
# other directory type.
TTLScope = Literal["user_events", "peer_events", "sessions"]
TTL_SCOPES: tuple[TTLScope, ...] = ("user_events", "peer_events", "sessions")


def _is_supported_directory_uri(uri: str) -> bool:
    """Accept only concrete events and sessions policy roots."""
    if not uri.startswith("viking://") or "?" in uri or "#" in uri:
        return False
    path = uri[len("viking://") :].rstrip("/")
    parts = path.split("/")
    if any(not part or part in {".", ".."} or "\\" in part for part in parts):
        return False
    if len(parts) < 3 or parts[0] != "user":
        return False
    if parts[2] == "sessions":
        # A session ID is an object root, not a configurable directory.
        return len(parts) == 3
    if len(parts) >= 4 and parts[2:4] == ["memories", "events"]:
        suffix = parts[4:]
    elif len(parts) >= 6 and parts[2] == "peers" and parts[4:6] == ["memories", "events"]:
        suffix = parts[6:]
    else:
        return False
    return not suffix


class TTLPolicy(BaseModel):
    """A single TTL policy node shared by the global default and each scope.

    - ``disabled``: explicitly no TTL; blocks lower-priority policies here.
    - ``days``: set object retention. ``ttl_days`` is then a required positive integer (minimum 1 day). "Off" is expressed with
      ``disabled``, never with ``0`` or a negative value.

    An absent policy node follows the configured priority chain. Inheritance is
    a default resolution behavior, not a configurable mode.
    """

    mode: Literal["disabled", "days"] = RuntimeField()
    ttl_days: Optional[StrictInt] = RuntimeField(default=None, ge=1, le=365000)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_ttl_days(self) -> "TTLPolicy":
        if self.mode == "days":
            if self.ttl_days is None:
                raise ValueError(
                    "ttl_days is required and must be a positive integer when mode='days'"
                )
        elif self.ttl_days is not None:
            raise ValueError(f"ttl_days must be omitted when mode='{self.mode}'")
        return self


class TTLConfig(BaseModel):
    """TTL policy tree. The global default is OFF.

    The default instance leaves the global policy ``disabled`` and type defaults
    unconfigured, so nothing expires unless an operator opts in. Unconfigured
    nodes resolve by priority. Current policy applies to existing objects without
    rewriting them; content writes renew event-file retention using modTime.
    """

    global_default: TTLPolicy = RuntimeField(
        default_factory=lambda: TTLPolicy(mode="disabled"),
        alias="global",
        description=("Library-global TTL default. Explicit policies use 'disabled' or 'days'."),
    )
    user_events: Optional[TTLPolicy] = RuntimeField(
        default=None,
        description="TTL default for viking://user/{user_id}/memories/events/.",
    )
    peer_events: Optional[TTLPolicy] = RuntimeField(
        default=None,
        description="TTL default for viking://user/{user_id}/peers/{peer_id}/memories/events/.",
    )
    sessions: Optional[TTLPolicy] = RuntimeField(
        default=None,
        description="TTL default for viking://user/{user_id}/sessions/.",
    )
    directories: Dict[str, TTLPolicy] = RuntimeField(
        default_factory=dict,
        description=(
            "TTL overrides keyed by a concrete in-scope Viking directory URI. "
            "Only events and sessions roots are configurable."
        ),
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def _check_global(self) -> "TTLConfig":
        normalized: Dict[str, TTLPolicy] = {}
        for raw_uri, policy in self.directories.items():
            uri = raw_uri.rstrip("/")
            if not _is_supported_directory_uri(uri):
                raise ValueError(
                    "ttl.directories keys must be concrete user events, peer events, "
                    "or sessions roots; child directories are read-only"
                )
            if uri in normalized:
                raise ValueError(f"duplicate ttl directory after normalization: {uri}")
            normalized[uri] = policy
        self.directories = normalized
        return self

    def resolve_uri_policy(self, uri: str, scope: TTLScope) -> TTLPolicy:
        """Resolve root > type > global; validated roots cannot nest."""
        normalized_uri = uri.rstrip("/")
        for directory, policy in self.directories.items():
            if normalized_uri.startswith(directory + "/") or normalized_uri == directory:
                return policy
        return getattr(self, scope) or self.global_default

    @property
    def enabled(self) -> bool:
        """True when TTL resolves to an active expiry for at least one scope."""
        return any(
            self.resolve_uri_policy("", scope).mode == "days" for scope in TTL_SCOPES
        ) or any(policy.mode == "days" for policy in self.directories.values())


class TTLCleanupConfig(BaseModel):
    """Cluster-only controls for physical deletion, separate from object expiry.

    Disabling is a rollout/incident pause for new physical deletes; expired content
    remains invisible. The Redis backend requires an index URL; the passive
    QueueFS backend uses the existing queue mount. These experiment settings are
    captured at initialization.
    """

    enabled: bool = RuntimeField(default=False)
    check_interval_seconds: float = RuntimeField(default=30.0, ge=1, le=86400)
    batch_size: StrictInt = RuntimeField(default=100, ge=1, le=10000)
    queue_backend: Literal["redis", "queuefs"] = RuntimeField(default="queuefs")
    execution: Literal["async", "sync"] = RuntimeField(default="async")
    index_url: Optional[str] = RuntimeField(default=None)
    index_namespace: str = RuntimeField(default="openviking:ttl:modtime")
    strategy: Literal["scheduled", "passive"] = RuntimeField(default="passive")
