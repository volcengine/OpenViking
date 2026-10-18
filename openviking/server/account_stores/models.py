# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Data structures returned by account storage operations."""
from typing import TypedDict


class DeletionRecord(TypedDict, total=False):
    task_id: str
    owner_account_id: str
    owner_user_id: str


class AccountSummary(TypedDict, total=False):
    account_id: str
    created_at: str
    user_count: int
    status: str
    task_id: str


class UserSummary(TypedDict, total=False):
    user_id: str
    role: str
    api_key: str
    key_prefix: str


class UsersPage(TypedDict):
    users: list[UserSummary]
    total: int
    account_total: int
    manager_count: int
    key_count: int


class GroupSummary(TypedDict):
    group_id: str
    member_count: int
