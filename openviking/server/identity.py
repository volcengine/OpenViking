# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Identity and role types for OpenViking multi-tenant HTTP Server."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from openviking.storage.viking_fs import VikingFS

from openviking_cli.session.user_id import UserIdentifier


class Role(str):
    """Role type — supports built-in roles and custom plugin-defined roles.

    Built-in roles (root/admin/user/global) are available as class attributes.
    Custom roles can be registered via ``Role.register(name, rank)``.

    GLOBAL is reserved for account-scoped identities. Its capabilities are
    orthogonal to ADMIN and must not be inferred from rank.

    Role comparison is string-based, so ``Role.ROOT == "root"`` and
    ``Role("custom") == "custom"`` both work.
    """

    ROOT = "root"
    ADMIN = "admin"
    USER = "user"
    GLOBAL = "global"

    # Legacy ranking for token role-downgrade checks, not request authorization.
    _BUILTIN_RANK: dict[str, int] = {
        USER: 0,
        ADMIN: 1,
        ROOT: 2,
    }
    _CUSTOM_RANK: dict[str, int] = {}

    @classmethod
    def register(cls, name: str, rank: int) -> None:
        """Register a custom role with a privilege rank.

        Args:
            name: Role identifier string.
            rank: Privilege rank — higher = more privilege.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("Custom role name must be a non-empty string")
        if name in (cls.ROOT, cls.ADMIN, cls.USER, cls.GLOBAL):
            raise ValueError("Built-in roles cannot be registered as custom roles")
        if not isinstance(rank, int) or isinstance(rank, bool):
            raise ValueError("Custom role rank must be an integer")
        cls._CUSTOM_RANK[name] = rank

    @classmethod
    def parse(cls, value: object) -> Role:
        """Validate a role before it enters an identity or request context.

        Construction remains string-compatible. Only enabled built-in roles
        and explicitly registered custom roles may become internal identities.
        GLOBAL remains reserved until its authorization rules are implemented.
        """
        if not isinstance(value, str) or (
            value not in (cls.ROOT, cls.ADMIN, cls.USER) and value not in cls._CUSTOM_RANK
        ):
            raise ValueError("Unknown or inactive identity role")
        return cls(value)

    @property
    def rank(self) -> int:
        """Return the legacy token rank; unknown roles have no implicit rank."""
        if self in self._BUILTIN_RANK:
            return self._BUILTIN_RANK[self]
        if self in self._CUSTOM_RANK:
            return self._CUSTOM_RANK[self]
        raise ValueError("Role has no legacy token rank")

    def allows_token_role(self, authorized_role: str) -> bool:
        """Check a saved authorization role against this registered role.

        This preserves legacy token compatibility, not operation permissions.
        Neither role is allowed to acquire an implicit rank.
        """
        return self.parse(authorized_role).rank <= self.rank

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Role):
            return str(self) == str(other)
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(str(self))


class AuthMode(str, Enum):
    """Authentication modes for OpenViking server.

    Built-in modes. Custom modes are plain strings not in this enum.
    """

    API_KEY = "api_key"
    TRUSTED = "trusted"
    DEV = "dev"


@dataclass
class ResolvedIdentity:
    """Output of auth middleware: raw identity resolved from API Key."""

    role: Role
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    # Trusted auth plugins may attach a request-scoped group snapshot. Empty
    # means callers should fall back to the server-managed group registry.
    group_ids: tuple[str, ...] = ()
    # True when this identity was minted from an OAuth-issued bearer token;
    # downstream checks (e.g. ROOT-requires-explicit-tenant headers) can skip
    # rules that target raw API-key auth, since OAuth claims already pin
    # account/user.
    from_oauth: bool = False

    def __post_init__(self) -> None:
        self.role = Role.parse(self.role)


@dataclass
class RequestContext:
    """Request-level context, flows through Router -> Service -> VikingFS."""

    user: UserIdentifier
    role: Role
    # Account-scoped groups resolved by the server for this request. Callers do
    # not supply these values through headers or ACL payloads.
    group_ids: tuple[str, ...] = ()
    # Request-level view filter for the current user's peers collection. This does
    # not change tenant/user identity or session ownership.
    actor_peer_id: Optional[str] = None
    # Mirrors ResolvedIdentity.from_oauth. Routes that mint OAuth state
    # (OTP issuance, oauth-verify) reject callers with from_oauth=True to
    # prevent a stolen access token from laundering itself into a long-lived
    # refresh-token chain.
    from_oauth: bool = False
    # Raw API key from the request — used by Connector to call back into OV
    # on behalf of the original user
    api_key: Optional[str] = field(default=None, repr=False)
    # Trusted background workers may maintain derived content without replaying
    # the triggering user's resource ACL. Tenant and namespace checks still apply.
    bypass_acl: bool = False

    def __post_init__(self) -> None:
        self.role = Role.parse(self.role)

    @property
    def account_id(self) -> str:
        return self.user.account_id


@dataclass
class ToolContext:
    """Tool-level context, containing request context and additional tool-specific information."""

    viking_fs: VikingFS
    request_ctx: RequestContext
    default_search_uris: List[str] = field(default_factory=list)
    transaction_handle: Optional[Any] = None
    read_file_contents: Optional[Any] = None  # 用于记录已读取的文件内容
    page_id_map: Optional[Any] = None  # PageIdMap for annotating read results

    @property
    def user(self):
        return self.request_ctx.user

    @property
    def role(self):
        return self.request_ctx.role

    @property
    def account_id(self) -> str:
        return self.request_ctx.user.account_id
