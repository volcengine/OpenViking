# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped Studio management proxy; model traffic uses the gateway port."""

import json
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from openviking.server.account_user_keys import list_users_with_keys, pick_user_with_key
from openviking.server.auth import (
    get_api_key_manager_or_raise,
    get_request_context,
    require_auth_root_or_admin,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.utils.config import get_openviking_config

router = APIRouter(prefix="/api/v1/admin/context-gateway", tags=["context-gateway"])

# Roles a gateway key may act as; root is never an account user.
KEY_USER_ROLES = {"user", "admin"}


async def bind_selected_user(request: Request, ctx: RequestContext, body: bytes) -> bytes:
    """Swap `user_id` in a key request for that user's key, so the browser never handles it."""
    try:
        value = json.loads(body)
    except ValueError:
        return body  # The gateway rejects it without echoing the input.
    if not isinstance(value, dict) or "user_id" not in value:
        return body
    if "openviking_key" in value:
        raise HTTPException(422, "Send either user_id or openviking_key, not both")
    if ctx.role == Role.ROOT:
        # Root may resolve to another account than the one Studio shows (api_key mode
        # resolves it to `default`), so a user ID could name someone else.
        raise HTTPException(
            400, "Root cannot choose a user; paste the user's OpenViking key instead"
        )
    rows = await list_users_with_keys(get_api_key_manager_or_raise(request), ctx.account_id)
    row = pick_user_with_key(
        [row for row in rows if row["role"] in KEY_USER_ROLES],
        value.pop("user_id"),
        missing="Unknown OpenViking user in this account",
        unreadable=(
            "This user's OpenViking key cannot be read on the server; "
            "paste the user's OpenViking key instead"
        ),
    )
    return json.dumps({**value, "openviking_key": row["api_key"]}).encode()


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
@require_auth_root_or_admin
async def proxy_context_gateway(
    path: str, request: Request, ctx: RequestContext = Depends(get_request_context)
):
    config = get_openviking_config().context_gateway
    if not config.enabled:
        raise HTTPException(503, "Context Gateway is not enabled")
    token = os.environ.get(config.admin_token_env, "")
    if len(token) < 32:
        raise HTTPException(503, "Context Gateway management token is not configured")
    # Only fixed management resources. No caller-supplied destination or identity.
    if (
        path.split("/", 1)[0]
        not in {"overview", "logs", "guides", "upstreams", "policies", "keys", "users", "tools"}
        or ".." in path
    ):
        raise HTTPException(404)
    headers = {
        "Authorization": "Bearer " + token,
        "X-OpenViking-Account": ctx.account_id,
        "Content-Type": "application/json",
    }
    body = await request.body()
    if request.method == "POST" and path == "keys":
        body = await bind_selected_user(request, ctx, body)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                config.url + "/admin/" + path,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.HTTPError:
        raise HTTPException(502, "Context Gateway management service is unavailable")
    return Response(
        upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
