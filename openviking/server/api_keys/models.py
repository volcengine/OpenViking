# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""共享的数据模型定义。"""

from dataclasses import dataclass, field
from typing import Dict

from openviking.server.identity import (
    ACCOUNT_USER_ASSIGNABLE_ROLES,
    ACCOUNT_USER_CREATION_ROLES,
    Role,
)
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError


def validate_account_user_role(role: str) -> Role:
    """Account users may be changed to USER or ADMIN; ROOT is the server identity."""
    resolved_role = Role(role)
    if resolved_role == Role.ROOT:
        raise PermissionDeniedError(
            "Account users cannot be assigned ROOT; use server.root_api_key for ROOT access."
        )
    if resolved_role not in ACCOUNT_USER_ASSIGNABLE_ROLES:
        raise InvalidArgumentError("Account user role must be user or admin.")
    return resolved_role


def validate_account_user_creation_role(role: str) -> Role:
    """Role accepted when creating an account user.

    Creation additionally accepts GLOBAL identities. ROOT remains reserved for
    the configured server identity, and role changes stay USER/ADMIN-only so a
    GLOBAL identity can never be promoted or demoted through the role API.
    """
    resolved_role = Role(role)
    if resolved_role == Role.ROOT:
        raise PermissionDeniedError(
            "Account users cannot be assigned ROOT; use server.root_api_key for ROOT access."
        )
    if resolved_role not in ACCOUNT_USER_CREATION_ROLES:
        raise InvalidArgumentError("Account user role must be user, admin, or global.")
    return resolved_role


@dataclass
class UserKeyEntry:
    """内存中的用户密钥索引条目。"""

    account_id: str
    user_id: str
    role: Role
    key_or_hash: str
    is_hashed: bool


@dataclass
class AccountInfo:
    """内存中的账户信息。"""

    created_at: str
    users: Dict[str, dict] = field(default_factory=dict)
    groups: Dict[str, dict] = field(default_factory=dict)
    groups_loaded: bool = True
    deletion: dict | None = None
