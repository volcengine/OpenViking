# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Authentication and authorization middleware for OpenViking multi-tenant HTTP Server."""

from typing import Optional

from fastapi import Depends, Header, Request

from openviking.core.peer_id import normalize_peer_id
from openviking.server.identity import (
    AuthMode,
    RequestContext,
    ResolvedIdentity,
    Role,
)
from openviking.telemetry.span_models import update_root_span_identity
from openviking_cli.exceptions import (
    InvalidArgumentError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import UserIdentifier


def _auth_mode(request: Request) -> str:
    config = getattr(request.app.state, "config", None)
    if config is not None and hasattr(config, "get_effective_auth_mode"):
        mode = config.get_effective_auth_mode()
        # AuthMode enum values are strings; custom modes are already strings
        return mode.value if isinstance(mode, AuthMode) else str(mode)
    return AuthMode.API_KEY.value


def _configured_root_api_key(request: Request) -> Optional[str]:
    config = getattr(request.app.state, "config", None)
    key = getattr(config, "root_api_key", None)
    return key if key != "" else None


def _extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if not isinstance(x_api_key, str):
        x_api_key = None
    if not isinstance(authorization, str):
        authorization = None
    if x_api_key:
        return x_api_key
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


def _normalize_header_value(value: Optional[str]) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _normalize_request_value(value: object) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def normalize_actor_peer_header(value: Optional[str]) -> Optional[str]:
    normalized = _normalize_header_value(value)
    if normalized and ("/" in normalized or "\\" in normalized):
        raise InvalidArgumentError("actor_peer_id must not contain path separators.")
    try:
        return normalize_peer_id(normalized)
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def _explicit_identity_from_request(request: Request) -> tuple[Optional[str], Optional[str]]:
    path_params = getattr(request, "path_params", {}) or {}
    query_params = request.query_params

    account_id = _normalize_request_value(path_params.get("account_id"))
    if account_id is None:
        account_id = _normalize_request_value(query_params.get("account_id"))

    user_id = _normalize_request_value(path_params.get("user_id"))
    if user_id is None:
        user_id = _normalize_request_value(query_params.get("user_id"))

    return account_id, user_id


def _get_plugin(request: Request):
    """Get the active auth plugin from app state."""
    plugin = getattr(request.app.state, "auth_plugin", None)
    if plugin is None:
        raise UnauthenticatedError("Auth plugin not initialized")
    return plugin


async def resolve_identity(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    x_openviking_account: Optional[str] = Header(None, alias="X-OpenViking-Account"),
    x_openviking_user: Optional[str] = Header(None, alias="X-OpenViking-User"),
) -> ResolvedIdentity:
    """Resolve API key to identity via the active auth plugin."""
    x_openviking_account = _normalize_header_value(x_openviking_account)
    x_openviking_user = _normalize_header_value(x_openviking_user)
    api_key = _extract_api_key(x_api_key, authorization)

    plugin = _get_plugin(request)
    return await plugin.resolve_identity(
        request,
        api_key=api_key,
        x_openviking_account=x_openviking_account,
        x_openviking_user=x_openviking_user,
    )


async def get_request_context(
    request: Request,
    identity: ResolvedIdentity = Depends(resolve_identity),
    x_openviking_actor_peer: Optional[str] = Header(None, alias="X-OpenViking-Actor-Peer"),
) -> RequestContext:
    """Convert ResolvedIdentity to RequestContext."""
    path = request.url.path
    plugin = _get_plugin(request)
    plugin.get_request_context_checks(path, identity)

    ctx = RequestContext(
        user=UserIdentifier(
            identity.account_id or "default",
            identity.user_id or "default",
        ),
        role=identity.role,
        actor_peer_id=normalize_actor_peer_header(x_openviking_actor_peer),
        from_oauth=identity.from_oauth,
    )
    # Update the unified root observability context after authentication succeeds.
    update_root_span_identity(
        request_state=request.state,
        account_id=identity.account_id,
        user_id=identity.user_id,
    )

    return ctx


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
                f"Requires role: {', '.join(str(r) for r in allowed_roles)}"
            )
        return ctx

    return Depends(_check)


# Convenience dependency factories for common role requirements
require_root = require_role(Role.ROOT)
require_admin = require_role(Role.ADMIN)
require_user = require_role(Role.USER)


_DEV_MODE_ADMIN_API_MESSAGE = (
    "Admin API requires api_key mode with root_api_key configured. Development mode does not "
    'support account or user management. You should set server.auth_mode = "api_key" in ov.conf'
)


def require_auth_role(*allowed_roles: Role):
    """Decorator for Admin API routes with mode-aware errors.

    Usage:
        @router.post("/admin/accounts")
        @require_auth_role(Role.ROOT)
        async def create_account(body: CreateAccountRequest, request: Request, ctx: RequestContext):
            ...
    """
    from functools import wraps

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request and ctx from kwargs or args
            request = kwargs.get("request")
            ctx = kwargs.get("ctx")

            # Find request and ctx in args if not in kwargs
            if request is None or ctx is None:
                import inspect

                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                request = bound_args.arguments.get("request")
                ctx = bound_args.arguments.get("ctx")

            if request is None or ctx is None:
                raise PermissionDeniedError(
                    "Admin API authentication failed: unable to resolve request context."
                )

            plugin = _get_plugin(request)
            manager = getattr(request.app.state, "api_key_manager", None)
            if manager is None and plugin.requires_api_key_manager():
                raise PermissionDeniedError(_DEV_MODE_ADMIN_API_MESSAGE)

            if ctx.role not in allowed_roles:
                raise PermissionDeniedError(
                    f"Requires role: {', '.join(str(r) for r in allowed_roles)}"
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# Convenience decorators for common admin role requirements
def require_auth_root(func):
    """Decorator to require ROOT role for Admin API."""
    return require_auth_role(Role.ROOT)(func)


def require_auth_admin(func):
    """Decorator to require ADMIN role for Admin API."""
    return require_auth_role(Role.ADMIN)(func)


def require_auth_user(func):
    """Decorator to require USER role for Admin API."""
    return require_auth_role(Role.USER)(func)


def require_auth_root_or_admin(func):
    """Decorator to require ROOT or ADMIN role for Admin API."""
    return require_auth_role(Role.ROOT, Role.ADMIN)(func)


def get_api_key_manager_or_raise(request: Request):
    """Get APIKeyManager from app state or raise appropriate error.

    Raises:
        PermissionDeniedError: When the current auth plugin requires an
            APIKeyManager but none is available.
    """
    manager = getattr(request.app.state, "api_key_manager", None)
    plugin = _get_plugin(request)
    if manager is None and plugin.requires_api_key_manager():
        raise PermissionDeniedError(_DEV_MODE_ADMIN_API_MESSAGE)
    return manager
