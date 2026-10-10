# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side lookup of a selected user's stored key; Studio only ever sends the user ID."""

from fastapi import HTTPException


async def list_users_with_keys(registry, account_id, role=None):
    """Users of the account, refreshed from the key store, with readable keys exposed."""
    await registry.refresh_account_users_from_store(account_id)
    return registry.get_users(account_id, limit=None, role_filter=role, expose_key=True)


def pick_user_with_key(rows, user_id, *, missing, unreadable):
    """The row of `user_id`. With key hashing only a hash is stored, so there is no key to use."""
    row = next((row for row in rows if row["user_id"] == user_id), None)
    if row is None:
        raise HTTPException(400, missing)
    if not row.get("api_key"):
        raise HTTPException(409, unreadable)
    return row
