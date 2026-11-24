# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility exports for the MySQL account-store schema."""

from openviking.server.account_stores.sql_schema import (
    Account,
    Base,
    Credential,
    Group,
    GroupMember,
    User,
)

__all__ = ["Account", "Base", "Credential", "Group", "GroupMember", "User"]
