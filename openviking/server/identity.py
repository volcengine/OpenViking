# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Identity and role types for OpenViking multi-tenant HTTP Server."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from openviking_cli.session.user_id import UserIdentifier


class Role(str, Enum):
    ROOT = "root"
    ADMIN = "admin"
    USER = "user"


@dataclass
class ResolvedIdentity:
    """Output of auth middleware: raw identity resolved from API Key."""

    role: Role
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None


@dataclass
class RequestContext:
    """Request-level context, flows through Router -> Service -> VikingFS."""

    user: UserIdentifier
    role: Role

    @property
    def account_id(self) -> str:
        return self.user.account_id
