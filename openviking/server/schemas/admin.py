# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/admin endpoints."""

from pydantic import BaseModel, ConfigDict


class AccountListItem(BaseModel):
    """Item shape of ``APIKeyManager.get_accounts()``."""

    model_config = ConfigDict(extra="allow")

    account_id: str
    created_at: str
    user_count: int = 0


class UserListItem(BaseModel):
    """Item shape of ``APIKeyManager.get_users(account_id)``."""

    model_config = ConfigDict(extra="allow")

    user_id: str
    role: str


class CreateAccountResult(BaseModel):
    """Result of ``POST /api/v1/admin/accounts``."""

    account_id: str
    admin_user_id: str
    user_key: str


class RegisterUserResult(BaseModel):
    """Result of ``POST /api/v1/admin/accounts/{account_id}/users``."""

    account_id: str
    user_id: str
    user_key: str


class SetUserRoleResult(BaseModel):
    """Result of ``PUT /api/v1/admin/accounts/{account_id}/users/{user_id}/role``."""

    account_id: str
    user_id: str
    role: str


class RegeneratedKeyResult(BaseModel):
    """Result of ``POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key``."""

    user_key: str


class DeletedFlagResult(BaseModel):
    """Trivial ``{"deleted": true}`` payload used by DELETE admin endpoints."""

    deleted: bool = True
