# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Authentication and authorization middleware for OpenViking multi-tenant HTTP Server."""

import hmac
from typing import Optional

from fastapi import Depends, Header, Request

from openviking.server.identity import AuthMode, RequestContext, ResolvedIdentity, Role
from openviking.telemetry.span_models import update_root_span_identity
from openviking_cli.exceptions import (
    InvalidArgumentError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import UserIdentifier

_ROOT_IMPLICIT_TENANT_ALLOWED_PATHS = {
    "/api/v1/system/status",
    "/api/v1/system/wait",
    "/api/v1/debug/health",
}
_ROOT_IMPLICIT_TENANT_ALLOWED_PREFIXES = (
    "/api/v1/admin",
    "/api/v1/observer",
)
_TRUSTED_RELAXED_IDENTITY_PREFIXES = ("/api/v1/admin",)


def _auth_mode(request: Request) -> AuthMode:
    config = getattr(request.app.state, "config", None)
    if config is not None and hasattr(config, "get_effective_auth_mode"):
        return config.get_effective_auth_mode()
    return AuthMode.API_KEY


def _root_request_requires_explicit_tenant(path: str) -> bool:
    """Return True when a ROOT request targets tenant-scoped data APIs.

    Root still needs access to admin and monitoring endpoints without a tenant
    context. For data APIs, implicit fallback to default/default is misleading,
    so callers must provide explicit account and user headers.
    """
    if path in _ROOT_IMPLICIT_TENANT_ALLOWED_PATHS:
        return False
    if path.startswith(_ROOT_IMPLICIT_TENANT_ALLOWED_PREFIXES):
        return False
    return True


def _trusted_request_requires_explicit_identity(path: str) -> bool:
    if path.startswith(_TRUSTED_RELAXED_IDENTITY_PREFIXES):
        return False
    return True


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


async def resolve_identity(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    x_openviking_account: Optional[str] = Header(None, alias="X-OpenViking-Account"),
    x_openviking_user: Optional[str] = Header(None, alias="X-OpenViking-User"),
    x_openviking_agent: Optional[str] = Header(None, alias="X-OpenViking-Agent"),
) -> ResolvedIdentity:
    """Resolve API key to identity.

    Strategy:
    - dev mode: no authentication, return implicit ROOT/default identity
    - trusted mode: trust explicit account/user headers, look up role from APIKeyManager
    - api_key mode: resolve via APIKeyManager (root key first, then user key index)
    """
    auth_mode = _auth_mode(request)
    api_key_manager = getattr(request.app.state, "api_key_manager", None)
    x_openviking_account = _normalize_header_value(x_openviking_account)
    x_openviking_user = _normalize_header_value(x_openviking_user)
    x_openviking_agent = _normalize_header_value(x_openviking_agent)
    api_key = _extract_api_key(x_api_key, authorization)

    if auth_mode == AuthMode.DEV:
        # Dev mode: no authentication, always return ROOT
        return ResolvedIdentity(
            role=Role.ROOT,
            account_id=x_openviking_account or "default",
            user_id=x_openviking_user or "default",
            agent_id=x_openviking_agent or "default",
        )

    if auth_mode == AuthMode.TRUSTED:
        configured_root_api_key = _configured_root_api_key(request)
        if configured_root_api_key:
            if not api_key:
                raise UnauthenticatedError(
                    "Missing API Key in trusted mode with Root API Key enabled."
                )
            if not hmac.compare_digest(api_key, configured_root_api_key):
                raise UnauthenticatedError(
                    "Invalid API Key in trusted mode with Root API Key enabled."
                )
        explicit_account_id, explicit_user_id = _explicit_identity_from_request(request)
        if (
            x_openviking_account
            and explicit_account_id
            and x_openviking_account != explicit_account_id
        ):
            raise InvalidArgumentError(
                "Trusted mode X-OpenViking-Account must match explicit account_id in the URL."
            )
        if x_openviking_user and explicit_user_id and x_openviking_user != explicit_user_id:
            raise InvalidArgumentError(
                "Trusted mode X-OpenViking-User must match explicit user_id in the URL."
            )

        effective_account_id = explicit_account_id or x_openviking_account
        effective_user_id = explicit_user_id or x_openviking_user

        # Check if this is an admin path without identity
        is_admin_path = request.url.path.startswith(_TRUSTED_RELAXED_IDENTITY_PREFIXES)
        if is_admin_path:
            return ResolvedIdentity(
                role=Role.ROOT,
                account_id="trusted",
                user_id="trusted",
                agent_id=x_openviking_agent or "default",
            )

        if _trusted_request_requires_explicit_identity(request.url.path):
            missing_fields = []
            if not effective_account_id:
                missing_fields.append("X-OpenViking-Account or explicit account_id in the URL")
            if not effective_user_id:
                missing_fields.append("X-OpenViking-User or explicit user_id in the URL")
            if missing_fields:
                raise InvalidArgumentError(
                    "Trusted mode requests must include " + " and ".join(missing_fields) + "."
                )

        trusted_role = Role.USER
        if api_key_manager and effective_account_id and effective_user_id:
            trusted_role = api_key_manager.get_user_role(effective_account_id, effective_user_id)

        return ResolvedIdentity(
            role=trusted_role,
            account_id=effective_account_id or "trusted",
            user_id=effective_user_id or "trusted",
            agent_id=x_openviking_agent or "default",
        )

    # AuthMode.API_KEY
    if api_key_manager is None:
        # This should not happen due to validate_server_config
        raise RuntimeError("api_key_manager not initialized in api_key mode")

    if not api_key:
        raise UnauthenticatedError("Missing API Key when resolving identity.")

    identity = api_key_manager.resolve(api_key)
    if identity.role == Role.ROOT:
        identity.account_id = x_openviking_account or identity.account_id or "default"
        identity.user_id = x_openviking_user or identity.user_id or "default"
        identity.agent_id = x_openviking_agent or identity.agent_id or "default"
        return identity

    identity.account_id = identity.account_id or "default"
    if x_openviking_account and x_openviking_account != identity.account_id:
        raise PermissionDeniedError(
            "X-OpenViking-Account cannot override the account for ADMIN/USER API keys."
        )

    if identity.role == Role.ADMIN:
        identity.user_id = x_openviking_user or identity.user_id or "default"
        identity.agent_id = x_openviking_agent or identity.agent_id or "default"
        return identity

    identity.user_id = identity.user_id or "default"
    if x_openviking_user and x_openviking_user != identity.user_id:
        raise PermissionDeniedError(
            "USER API keys cannot override X-OpenViking-User; the effective user is derived "
            "from the key."
        )
    identity.agent_id = x_openviking_agent or identity.agent_id or "default"
    return identity


async def get_request_context(
    request: Request,
    identity: ResolvedIdentity = Depends(resolve_identity),
) -> RequestContext:
    """Convert ResolvedIdentity to RequestContext."""
    path = request.url.path
    auth_mode = _auth_mode(request)
    api_key_manager = getattr(request.app.state, "api_key_manager", None)
    if (
        auth_mode == AuthMode.API_KEY
        and api_key_manager is not None
        and identity.role == Role.ROOT
        and _root_request_requires_explicit_tenant(path)
    ):
        account_header = request.headers.get("X-OpenViking-Account")
        user_header = request.headers.get("X-OpenViking-User")
        if not account_header or not user_header:
            raise InvalidArgumentError(
                "ROOT requests to tenant-scoped APIs must include X-OpenViking-Account "
                "and X-OpenViking-User headers. Use a user key for regular data access."
            )

    if auth_mode == AuthMode.TRUSTED:
        is_admin_path = path.startswith(_TRUSTED_RELAXED_IDENTITY_PREFIXES)
        if not is_admin_path:
            if not identity.account_id:
                raise InvalidArgumentError(
                    "Trusted mode requests must include X-OpenViking-Account."
                )
            if not identity.user_id:
                raise InvalidArgumentError("Trusted mode requests must include X-OpenViking-User.")

    ctx = RequestContext(
        user=UserIdentifier(
            identity.account_id or "default",
            identity.user_id or "default",
            identity.agent_id or "default",
        ),
        role=identity.role,
        namespace_policy=(
            api_key_manager.get_account_policy(identity.account_id)
            if api_key_manager is not None and hasattr(api_key_manager, "get_account_policy")
            else identity.namespace_policy
        ),
    )
    # Update the unified root observability context after authentication succeeds.
    update_root_span_identity(
        request_state=request.state,
        account_id=identity.account_id,
        user_id=identity.user_id,
        agent_id=identity.agent_id,
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
                f"Requires role: {', '.join(r.value for r in allowed_roles)}"
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

            if request is None:
                raise RuntimeError("require_auth_role decorator requires 'request' parameter")
            if ctx is None:
                raise RuntimeError("require_auth_role decorator requires 'ctx' parameter")

            # Check auth mode
            auth_mode = _auth_mode(request)

            # In trusted mode, we allow access even without api_key_manager
            # as long as the role requirement is satisfied
            manager = getattr(request.app.state, "api_key_manager", None)
            if manager is None and auth_mode != AuthMode.TRUSTED:
                raise PermissionDeniedError(_DEV_MODE_ADMIN_API_MESSAGE)

            if ctx.role not in allowed_roles:
                raise PermissionDeniedError(
                    f"Requires role: {', '.join(r.value for r in allowed_roles)}"
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# Convenience decorators for common admin role requirements
def require_auth_root(func):
    """Decorator to require ROOT role for Admin API.

    Usage:
        @router.post("/admin/accounts")
        @require_auth_root
        async def create_account(body: CreateAccountRequest, request: Request, ctx: RequestContext):
            ...
    """
    return require_auth_role(Role.ROOT)(func)


def require_auth_admin(func):
    """Decorator to require ADMIN role for Admin API.

    Usage:
        @router.post("/admin/accounts")
        @require_auth_admin
        async def create_account(body: CreateAccountRequest, request: Request, ctx: RequestContext):
            ...
    """
    return require_auth_role(Role.ADMIN)(func)


def require_auth_user(func):
    """Decorator to require USER role for Admin API.

    Usage:
        @router.post("/admin/accounts")
        @require_auth_user
        async def create_account(body: CreateAccountRequest, request: Request, ctx: RequestContext):
            ...
    """
    return require_auth_role(Role.USER)(func)


def require_auth_root_or_admin(func):
    """Decorator to require ROOT or ADMIN role for Admin API.

    Usage:
        @router.post("/admin/accounts")
        @require_auth_root_or_admin
        async def create_account(body: CreateAccountRequest, request: Request, ctx: RequestContext):
            ...
    """
    return require_auth_role(Role.ROOT, Role.ADMIN)(func)


def get_api_key_manager_or_raise(request: Request):
    """Get APIKeyManager from app state or raise appropriate error.

    Raises:
        PermissionDeniedError: In dev mode without API key manager.
    """
    manager = getattr(request.app.state, "api_key_manager", None)
    auth_mode = _auth_mode(request)
    if manager is None and auth_mode != AuthMode.TRUSTED:
        raise PermissionDeniedError(_DEV_MODE_ADMIN_API_MESSAGE)
    return manager
