# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Build callback credentials for services that write to OpenViking."""

from fastapi import HTTPException, Request, status

from openviking.server.auth import _auth_mode
from openviking.server.config import get_server_url_from_server_data
from openviking.server.identity import RequestContext

DEFAULT_AGENT_ID = "web-playground"
DEFAULT_NAMESPACE_POLICY = {
    "isolate_user_scope_by_agent": False,
    "isolate_agent_scope_by_user": False,
}


def attach_openviking_connection(
    body: dict,
    request: Request,
    ctx: RequestContext,
    *,
    include_legacy_user_id: bool = True,
) -> dict:
    """Attach the authenticated caller identity used for callbacks into OV."""
    enriched = dict(body)
    plugin = getattr(request.app.state, "auth_plugin", None)
    auth_mode = _auth_mode(request)
    api_key = ctx.api_key or ""
    if not api_key and plugin is not None and plugin.can_skip_api_key_for_bot_proxy():
        if auth_mode != "trusted":
            if include_legacy_user_id:
                enriched.setdefault("user_id", ctx.user.user_id)
            return enriched
    elif not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Request requires a forwardable OpenViking API key.",
        )

    connection = {
        "account_id": ctx.account_id,
        "user_id": ctx.user.user_id,
        "agent_id": DEFAULT_AGENT_ID,
        "role": str(ctx.role),
        "api_key_type": "root" if auth_mode == "trusted" else "user",
        "server_url": get_server_url_from_server_data(getattr(request.app.state, "config", None)),
        "namespace_policy": dict(DEFAULT_NAMESPACE_POLICY),
    }
    if api_key:
        connection["api_key"] = api_key
    if ctx.actor_peer_id:
        connection["actor_peer_id"] = ctx.actor_peer_id
    enriched["openviking_connection"] = connection
    return enriched


__all__ = ["attach_openviking_connection"]
