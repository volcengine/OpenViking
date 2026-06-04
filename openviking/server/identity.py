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


class Role(str, Enum):
    ROOT = "root"
    ADMIN = "admin"
    USER = "user"


class AuthMode(str, Enum):
    """Authentication modes for OpenViking server."""

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
