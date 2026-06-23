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

    Built-in roles (root/admin/user) are available as class attributes.
    Custom roles can be registered via ``Role.register(name, rank)``.

    Role comparison is string-based, so ``Role.ROOT == "root"`` and
    ``Role("custom") == "custom"`` both work.
    """

    ROOT = "root"
    ADMIN = "admin"
    USER = "user"

    # Privilege ranking for role-downgrade detection.
    # Higher rank => more privilege.
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
        cls._CUSTOM_RANK[name] = rank

    @property
    def rank(self) -> int:
        """Return the privilege rank for this role."""
        return self._BUILTIN_RANK.get(self, self._CUSTOM_RANK.get(self, 0))

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
    # True when this identity was minted from an OAuth-issued bearer token;
    # downstream checks (e.g. ROOT-requires-explicit-tenant headers) can skip
    # rules that target raw API-key auth, since OAuth claims already pin
    # account/user.
    from_oauth: bool = False


@dataclass
class RequestContext:
    """Request-level context, flows through Router -> Service -> VikingFS."""

    user: UserIdentifier
    role: Role
    # Request-level view filter for the current user's peers collection. This does
    # not change tenant/user identity or session ownership.
    actor_peer_id: Optional[str] = None
    # Set only when the request came through the legacy agent_id compatibility
    # path. Search/find use it to include unmigrated viking://agent data without
    # making pure actor_peer_id requests read legacy agent scopes by default.
    legacy_agent_id: Optional[str] = None
    # Mirrors ResolvedIdentity.from_oauth. Routes that mint OAuth state
    # (OTP issuance, oauth-verify) reject callers with from_oauth=True to
    # prevent a stolen access token from laundering itself into a long-lived
    # refresh-token chain.
    from_oauth: bool = False

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
