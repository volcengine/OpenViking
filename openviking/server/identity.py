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


@dataclass
class RequestContext:
    """Request-level context, flows through Router -> Service -> VikingFS."""

    user: UserIdentifier
    role: Role
    namespace_policy: AccountNamespacePolicy = field(default_factory=AccountNamespacePolicy)

    @property
    def account_id(self) -> str:
        return self.user.account_id


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
