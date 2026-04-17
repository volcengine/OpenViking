# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Identity and role types for OpenViking multi-tenant HTTP Server."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from openviking_cli.session.user_id import UserIdentifier


class Role(str, Enum):
    ROOT = "root"
    ADMIN = "admin"
    USER = "user"


class AuthMode(str, Enum):
    """Authentication modes for OpenViking server."""

    API_KEY = "api_key"
    TRUSTED = "trusted"
    DEV = "dev"


DATA_READ_PERMISSION = "data.read"
DATA_WRITE_PERMISSION = "data.write"
DEFAULT_PERMISSION_PROFILE_ID = "data_rw"
READ_ONLY_PERMISSION_PROFILE_ID = "data_readonly"
NO_DATA_ACCESS_PERMISSION_PROFILE_ID = "no_data_access"


@dataclass(frozen=True)
class EffectivePermissions:
    """Effective data API permissions carried through the request context."""

    data_read: bool = True
    data_write: bool = True

    @classmethod
    def full_access(cls) -> "EffectivePermissions":
        return cls(data_read=True, data_write=True)

    @classmethod
    def read_only(cls) -> "EffectivePermissions":
        return cls(data_read=True, data_write=False)

    @classmethod
    def no_access(cls) -> "EffectivePermissions":
        return cls(data_read=False, data_write=False)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "EffectivePermissions":
        if not isinstance(data, dict):
            return cls.full_access()
        return cls(
            data_read=bool(data.get("data_read", True)),
            data_write=bool(data.get("data_write", True)),
        )

    def to_dict(self) -> dict:
        return {
            "data_read": self.data_read,
            "data_write": self.data_write,
        }

    def allows(self, permission: str) -> bool:
        if permission == DATA_READ_PERMISSION:
            return self.data_read
        if permission == DATA_WRITE_PERMISSION:
            return self.data_write
        return False


@dataclass(frozen=True)
class PermissionProfile:
    """Named account-level permission profile."""

    profile_id: str
    permissions: EffectivePermissions
    builtin: bool = False

    @classmethod
    def from_dict(
        cls,
        profile_id: str,
        data: Optional[dict],
        *,
        builtin: bool = False,
    ) -> "PermissionProfile":
        return cls(
            profile_id=profile_id,
            permissions=EffectivePermissions.from_dict(data),
            builtin=builtin,
        )

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "builtin": self.builtin,
            **self.permissions.to_dict(),
        }


def get_builtin_permission_profiles() -> dict[str, PermissionProfile]:
    """Return assignable built-in permission profiles."""
    return {
        DEFAULT_PERMISSION_PROFILE_ID: PermissionProfile(
            profile_id=DEFAULT_PERMISSION_PROFILE_ID,
            permissions=EffectivePermissions.full_access(),
            builtin=True,
        ),
        READ_ONLY_PERMISSION_PROFILE_ID: PermissionProfile(
            profile_id=READ_ONLY_PERMISSION_PROFILE_ID,
            permissions=EffectivePermissions.read_only(),
            builtin=True,
        ),
        NO_DATA_ACCESS_PERMISSION_PROFILE_ID: PermissionProfile(
            profile_id=NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
            permissions=EffectivePermissions.no_access(),
            builtin=True,
        ),
    }


@dataclass(frozen=True)
class AccountNamespacePolicy:
    """Account-level namespace isolation policy."""

    isolate_user_scope_by_agent: bool = False
    isolate_agent_scope_by_user: bool = False

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "AccountNamespacePolicy":
        if not isinstance(data, dict):
            return cls()
        return cls(
            isolate_user_scope_by_agent=bool(data.get("isolate_user_scope_by_agent", False)),
            isolate_agent_scope_by_user=bool(data.get("isolate_agent_scope_by_user", False)),
        )

    def to_dict(self) -> dict:
        return {
            "isolate_user_scope_by_agent": self.isolate_user_scope_by_agent,
            "isolate_agent_scope_by_user": self.isolate_agent_scope_by_user,
        }


@dataclass
class ResolvedIdentity:
    """Output of auth middleware: raw identity resolved from API Key."""

    role: Role
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    namespace_policy: AccountNamespacePolicy = field(default_factory=AccountNamespacePolicy)
    permission_profile: Optional[str] = None
    effective_permissions: EffectivePermissions = field(
        default_factory=EffectivePermissions.full_access
    )


@dataclass
class RequestContext:
    """Request-level context, flows through Router -> Service -> VikingFS."""

    user: UserIdentifier
    role: Role
    namespace_policy: AccountNamespacePolicy = field(default_factory=AccountNamespacePolicy)
    permission_profile: Optional[str] = None
    effective_permissions: EffectivePermissions = field(
        default_factory=EffectivePermissions.full_access
    )

    @property
    def account_id(self) -> str:
        return self.user.account_id

    def has_permission(self, permission: str) -> bool:
        if self.role in {Role.ROOT, Role.ADMIN}:
            return True
        return self.effective_permissions.allows(permission)


@dataclass
class ToolContext:
    """Tool-level context, containing request context and additional tool-specific information."""

    request_ctx: RequestContext
    default_search_uris: List[str] = field(default_factory=list)
    transaction_handle: Optional[Any] = None

    @property
    def user(self):
        return self.request_ctx.user

    @property
    def role(self):
        return self.request_ctx.role

    @property
    def account_id(self) -> str:
        return self.request_ctx.user.account_id

    @property
    def permission_profile(self):
        return self.request_ctx.permission_profile

    @property
    def effective_permissions(self):
        return self.request_ctx.effective_permissions

    def has_permission(self, permission: str) -> bool:
        return self.request_ctx.has_permission(permission)
