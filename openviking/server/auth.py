# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Authentication and authorization middleware for OpenViking multi-tenant HTTP Server."""

from typing import Optional

from fastapi import Depends, Header, Request

from openviking.server.identity import RequestContext, ResolvedIdentity, Role
from openviking_cli.exceptions import PermissionDeniedError, UnauthenticatedError
from openviking_cli.session.user_id import UserIdentifier


async def resolve_identity(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    x_openviking_agent: Optional[str] = Header(None, alias="X-OpenViking-Agent"),
) -> ResolvedIdentity:
    """Resolve API key to identity.

    Strategy:
    - If api_key_manager is None (dev mode): return ROOT with default identity
    - Otherwise: resolve via APIKeyManager (root key first, then user key index)
    """
    api_key_manager = getattr(request.app.state, "api_key_manager", None)

    if api_key_manager is None:
        return ResolvedIdentity(
            role=Role.ROOT,
            account_id="default",
            user_id="default",
            agent_id=x_openviking_agent or "default",
        )

    # Extract API key from request
    api_key = x_api_key
    if not api_key and authorization:
        if authorization.startswith("Bearer "):
            api_key = authorization[7:]

    if not api_key:
        raise UnauthenticatedError("Missing API Key")

    identity = api_key_manager.resolve(api_key)
    identity.agent_id = x_openviking_agent or "default"
    return identity


async def get_request_context(
    identity: ResolvedIdentity = Depends(resolve_identity),
) -> RequestContext:
    """Convert ResolvedIdentity to RequestContext."""
    return RequestContext(
        user=UserIdentifier(
            identity.account_id or "default",
            identity.user_id or "default",
            identity.agent_id or "default",
        ),
        role=identity.role,
    )


def require_role(*allowed_roles: Role):
    """Dependency factory that checks role permission.

    Usage:
        @router.post("/admin/accounts")
        async def create_account(ctx: RequestContext = Depends(require_role(Role.ROOT))):
            ...
    """

    async def _check(ctx: RequestContext = Depends(get_request_context)):
        if ctx.role not in allowed_roles:
            raise PermissionDeniedError(
                f"Requires role: {', '.join(r.value for r in allowed_roles)}"
            )
        return ctx

    return Depends(_check)
