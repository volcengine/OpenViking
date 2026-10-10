# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Called by OpenViking's durable deletion consumer; failures remain retryable."""

import os
from urllib.parse import quote

import httpx


async def delete_gateway_data(config, account_id, user_id=None):
    if not config.enabled:
        return
    token = os.environ.get(config.admin_token_env, "")
    if len(token) < 32:
        raise RuntimeError("OpenViking Gateway management token is not configured")
    path = f"users/{quote(user_id, safe='')}/data" if user_id is not None else "account/data"
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.delete(
            config.url + "/admin/" + path,
            headers={"Authorization": "Bearer " + token, "X-OpenViking-Account": account_id},
        )
        if response.status_code != 200:
            raise RuntimeError(f"OpenViking Gateway deletion failed (HTTP {response.status_code})")
