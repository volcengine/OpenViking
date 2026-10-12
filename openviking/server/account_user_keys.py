# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side user selection with optional stored-key requirements."""

from fastapi import HTTPException


async def list_account_users(registry, account_id, role=None, *, expose_key=True):
    """Refresh account users, exposing readable keys only when requested."""
    await registry.refresh_account_users_from_store(account_id)
    return registry.get_users(account_id, limit=None, role_filter=role, expose_key=expose_key)


def pick_account_user(rows, user_id, *, missing, unreadable, require_key=True):
    """Select a user, requiring a readable key unless trusted transport supplies its own."""
    row = next((row for row in rows if row["user_id"] == user_id), None)
    if row is None:
        raise HTTPException(400, missing)
    if require_key and not row.get("api_key"):
        raise HTTPException(409, unreadable)
    return row
