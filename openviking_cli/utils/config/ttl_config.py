# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL (time-to-live) policy configuration for events, sessions and resources.

TTL is default OFF and only ever applies to four supported directory scopes:

- ``user_events``  -> ``viking://user/{user_id}/memories/events/``
- ``peer_events``  -> ``viking://user/{user_id}/peers/{peer_id}/memories/events/``
- ``resources``    -> public, user and peer resource import roots
- ``sessions``     -> ``viking://user/{user_id}/sessions/``

It is not extended to preferences, entities, or any other directory.

The configuration mirrors the design doc's minimal ``policy`` protocol: a library
global default, per-scope defaults, and optional concrete-directory overrides.
All levels use the same ``TTLPolicy`` structure. Events and sessions resolve as
*nearest directory override > scope default > library global default > off*.
Resources are long-term memory and resolve as *nearest directory override >
resource default > off*; they never inherit the library global default.
"""

from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field, StrictInt, model_validator

from .runtime_field import RuntimeField

# The supported TTL scopes. Deliberately closed: TTL never applies to any
# other directory type.
TTLScope = Literal["user_events", "peer_events", "sessions", "resources"]
TTL_SCOPES: tuple[TTLScope, ...] = ("user_events", "peer_events", "sessions", "resources")


def _is_supported_directory_uri(uri: str) -> bool:
    """Accept directory policies, never an individual event or session."""
    if not uri.startswith("viking://") or "?" in uri or "#" in uri:
        return False
    path = uri[len("viking://") :].rstrip("/")
    parts = path.split("/")
    if any(not part or part in {".", ".."} or "\\" in part for part in parts):
        return False
    if parts[0] == "resources":
        return True
    if len(parts) < 3 or parts[0] != "user":
        return False
    if parts[2] == "resources" or (
        len(parts) >= 5 and parts[2] == "peers" and parts[4] == "resources"
    ):
        return True
    if parts[2] == "sessions":
        # A session ID is an object root, not a configurable directory.
        return len(parts) == 3
    if len(parts) >= 4 and parts[2:4] == ["memories", "events"]:
        return True
    elif (
        len(parts) >= 6
        and parts[2] == "peers"
        and parts[4:6]
        == [
            "memories",
            "events",
        ]
    ):
        return True
    else:
        return False


class TTLPolicy(BaseModel):
    """A single TTL policy node shared by the global default and each scope.

    - ``inherit``: defer to the next explicit ancestor, then scope -> global ->
      off. Valid for directories and scope defaults, never for the library global.
    - ``disabled``: explicitly no TTL; blocks inheritance from the global level.
    - ``days``: expire ``ttl_days`` after the object's latest successful content
      update. ``ttl_days`` is then a required positive integer (minimum 1 day). "Off" is expressed with
      ``disabled``, never with ``0`` or a negative value.
    """

    mode: Literal["inherit", "disabled", "days", "absolute"] = RuntimeField(default="inherit")
    ttl_absolute: Optional[StrictInt] = RuntimeField(default=None, ge=1, le=253402300799)
    ttl_days: Optional[StrictInt] = RuntimeField(default=None, ge=1, le=365000)

    @model_validator(mode="after")
    def _check_ttl_days(self) -> "TTLPolicy":
        if (self.mode == "absolute") != (self.ttl_absolute is not None):
            raise ValueError("ttl_absolute is required only for mode=absolute")
        if self.mode == "days":
            if self.ttl_days is None:
                raise ValueError(
                    "ttl_days is required and must be a positive integer when mode='days'"
                )
        elif self.ttl_days is not None:
            raise ValueError(f"ttl_days must be omitted when mode='{self.mode}'")
        return self


class TTLConfig(BaseModel):
    """Cluster-wide TTL policy. Default OFF.

    The default instance leaves the global and resource policies ``disabled``
    and the event/session scopes ``inherit``, so nothing expires unless an
    operator opts in. Changing this config only affects objects created
    afterwards. Existing managed objects retain their snapshotted duration,
    and relative deadlines renew when their content is successfully updated.
    """

    global_default: TTLPolicy = RuntimeField(
        default_factory=lambda: TTLPolicy(mode="disabled"),
        alias="global",
        description=(
            "Library-global TTL default. Only 'disabled' or 'days' are allowed here; "
            "'inherit' has nothing above it to inherit from."
        ),
    )
    user_events: TTLPolicy = RuntimeField(
        default_factory=TTLPolicy,
        description="TTL default for viking://user/{user_id}/memories/events/.",
    )
    peer_events: TTLPolicy = RuntimeField(
        default_factory=TTLPolicy,
        description="TTL default for viking://user/{user_id}/peers/{peer_id}/memories/events/.",
    )
    sessions: TTLPolicy = RuntimeField(
        default_factory=TTLPolicy,
        description="TTL default for viking://user/{user_id}/sessions/.",
    )
    resources: TTLPolicy = RuntimeField(
        default_factory=lambda: TTLPolicy(mode="disabled"),
        description="Separate resource TTL default; never inherits the library global policy.",
    )
    directories: Dict[str, TTLPolicy] = RuntimeField(
        default_factory=dict,
        description=(
            "TTL overrides keyed by a concrete in-scope Viking directory URI. "
            "The nearest matching ancestor wins."
        ),
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _check_global(self) -> "TTLConfig":
        if self.global_default.mode not in {"disabled", "days"}:
            raise ValueError(
                "ttl.global mode must be 'disabled' or 'days' ('inherit' is not "
                "allowed at the global level)"
            )
        if any(
            getattr(self, scope).mode == "absolute"
            for scope in ("user_events", "peer_events", "sessions")
        ):
            raise ValueError("absolute TTL policies are supported only for resources")
        normalized: Dict[str, TTLPolicy] = {}
        for raw_uri, policy in self.directories.items():
            uri = raw_uri.rstrip("/")
            if not _is_supported_directory_uri(uri):
                raise ValueError(
                    "ttl.directories keys must be concrete directories under "
                    "user events, peer events, sessions, or resources"
                )
            if uri in normalized:
                raise ValueError(f"duplicate ttl directory after normalization: {uri}")
            parts = uri.removeprefix("viking://").split("/")
            resource = (
                parts[0] == "resources"
                or (len(parts) >= 3 and parts[2] == "resources")
                or (len(parts) >= 5 and parts[2] == "peers" and parts[4] == "resources")
            )
            if policy.mode == "absolute" and not resource:
                raise ValueError("absolute TTL policies are supported only for resources")
            normalized[uri] = policy
        self.directories = normalized
        return self

    def resolve_scope(self, scope: TTLScope) -> Optional[int]:
        """Return the effective ``ttl_days`` for a scope, or ``None`` when off.

        The scope's own policy wins. For events and sessions, ``inherit`` falls
        through to the global default; resources never inherit that default.
        ``disabled`` blocks inheritance and yields off.
        """
        policy = getattr(self, scope)
        if policy.mode == "days":
            return policy.ttl_days
        if policy.mode != "inherit" or scope == "resources":
            return None
        # mode == "inherit": fall through to the global default.
        if self.global_default.mode == "days":
            return self.global_default.ttl_days
        return None

    def resolve_uri(self, uri: str, scope: TTLScope) -> Optional[int]:
        """Resolve one in-scope URI using its nearest directory override.

        URI scope validation intentionally stays in :mod:`openviking.core.ttl`;
        this config model only performs boundary-safe ancestor matching. This
        keeps configuration parsing independent from server-side URI modules.
        """
        policy = self.resolve_uri_policy(uri, scope)
        return policy.ttl_days if policy.mode == "days" else None

    def resolve_uri_policy(self, uri: str, scope: TTLScope) -> TTLPolicy:
        normalized_uri = uri.rstrip("/")
        matches = (
            (directory, policy)
            for directory, policy in self.directories.items()
            if normalized_uri.startswith(directory + "/")
            or (scope == "resources" and normalized_uri == directory)
        )
        for _, policy in sorted(matches, key=lambda item: len(item[0]), reverse=True):
            if policy.mode != "inherit":
                return policy
        policy = getattr(self, scope)
        if scope == "resources" and policy.mode == "inherit":
            return TTLPolicy(mode="disabled")
        return self.global_default if policy.mode == "inherit" else policy

    @property
    def enabled(self) -> bool:
        """True when TTL resolves to an active expiry for at least one scope."""
        return (
            self.resources.mode == "absolute"
            or any(self.resolve_scope(scope) is not None for scope in TTL_SCOPES)
            or any(policy.mode in {"days", "absolute"} for policy in self.directories.values())
        )


class TTLCleanupConfig(BaseModel):
    """Cluster-only controls for physical deletion, separate from object expiry.

    Disabling is a rollout/incident pause for new physical deletes; expired L2
    remains invisible. The default keeps the cleanup behavior enabled whenever
    an object has an expiry snapshot.
    """

    enabled: bool = RuntimeField(default=True)
    check_interval_seconds: float = RuntimeField(default=30.0, ge=1, le=86400)
    scan_jitter_seconds: float = RuntimeField(default=5.0, ge=0, le=86400)
    cleanup_jitter_seconds: float = RuntimeField(default=86400.0, ge=0, le=86400)
    batch_size: StrictInt = RuntimeField(default=100, ge=1, le=10000)
    max_batch_bytes: StrictInt = RuntimeField(default=1_048_576, ge=1024, le=104_857_600)
    scan_time_budget_seconds: float = RuntimeField(default=5.0, gt=0, le=300)


class ResourceTTL(BaseModel):
    """Public resource TTL: relative whole days or an absolute Unix timestamp in seconds."""

    ttl_relative: Optional[StrictInt] = Field(default=None, ge=1, le=365000)
    ttl_absolute: Optional[StrictInt] = Field(default=None, ge=1, le=253402300799)

    @model_validator(mode="after")
    def _exclusive(self) -> "ResourceTTL":
        if self.ttl_relative is not None and self.ttl_absolute is not None:
            raise ValueError("ttl_relative and ttl_absolute are mutually exclusive")
        return self

    def policy(self) -> TTLPolicy:
        if self.ttl_relative is not None:
            return TTLPolicy(mode="days", ttl_days=self.ttl_relative)
        if self.ttl_absolute is not None:
            return TTLPolicy(mode="absolute", ttl_absolute=self.ttl_absolute)
        return TTLPolicy(mode="disabled")


class DocumentTTL(BaseModel):
    """Set one live file's relative retention or fixed ISO 8601 deadline."""

    expires_at: Optional[str] = None
    ttl_relative: Optional[StrictInt] = Field(default=None, ge=1, le=365000)

    @model_validator(mode="after")
    def _one_policy(self) -> "DocumentTTL":
        if (self.expires_at is None) == (self.ttl_relative is None):
            raise ValueError("provide exactly one of expires_at or ttl_relative")
        return self
