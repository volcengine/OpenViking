# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""API key mode authentication plugin."""

from __future__ import annotations

import sys
from typing import Optional

from fastapi import Request

from openviking.server.api_keys import APIKeyManager
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import PermissionDeniedError, UnauthenticatedError

_API_KEY_ROOT_ALLOWED_PATHS = {
    "/api/v1/system/status",
    "/api/v1/system/wait",
    "/api/v1/debug/health",
}
_API_KEY_ROOT_ALLOWED_PREFIXES = (
    "/api/v1/admin",
    "/api/v1/observer",
    "/api/v1/console",
    "/api/v1/tasks",
    "/api/v1/system/backend",
    "/api/v1/system/sync",
)


def _api_key_root_can_access_path(path: str) -> bool:
    if path in _API_KEY_ROOT_ALLOWED_PATHS:
        return True
    if path.startswith(_API_KEY_ROOT_ALLOWED_PREFIXES):
        return True
    return False


def _remove_header(request: Request, name: bytes) -> None:
    """Remove a header from the underlying request scope.

    Starlette's Headers object is immutable; we mutate the raw scope list
    so downstream middleware and handlers do not see the header.
    """
    scope_headers = request.scope.get("headers", [])
    request.scope["headers"] = [
        (key, value) for key, value in scope_headers if key.lower() != name.lower()
    ]


class ApiKeyAuthPlugin(AuthPlugin):
    """API key mode: resolve identity via APIKeyManager.

    Supports OAuth bearer tokens layered on top of API keys.
    """

    auth_mode = "api_key"

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        api_key_manager = getattr(request.app.state, "api_key_manager", None)
        if api_key_manager is None:
            raise RuntimeError("api_key_manager not initialized in api_key mode")

        if not api_key:
            raise UnauthenticatedError("Missing API Key when resolving identity.")

        # OAuth 2.1 fast path
        oauth_identity = await self._try_resolve_oauth_token(
            request,
            api_key,
            x_openviking_account=x_openviking_account,
            x_openviking_user=x_openviking_user,
        )
        if oauth_identity is not None:
            return oauth_identity

        identity = api_key_manager.resolve(api_key)
        identity.account_id = identity.account_id or "default"
        identity.user_id = identity.user_id or "default"

        # Silently ignore identity assertion headers in api_key mode.
        # Older clients may send these headers out of habit; clearing them
        # avoids breaking compatibility without weakening security.
        if x_openviking_account:
            _remove_header(request, b"x-openviking-account")
        if x_openviking_user:
            _remove_header(request, b"x-openviking-user")

        return identity

    async def _try_resolve_oauth_token(
        self,
        request: Request,
        api_key: str,
        *,
        x_openviking_account: Optional[str],
        x_openviking_user: Optional[str],
    ) -> Optional[ResolvedIdentity]:
        """Attempt to verify the bearer as an OAuth-issued opaque access token.

        Returns the resolved identity on success. Returns None when the bearer
        doesn't carry the OAuth access-token prefix or when OAuth isn't enabled.
        Raises UnauthenticatedError when the bearer IS prefix-tagged but lookup
        fails — fail-closed.
        """
        from openviking.server.oauth.provider import ACCESS_TOKEN_PREFIX

        if not api_key.startswith(ACCESS_TOKEN_PREFIX):
            return None
        provider = getattr(request.app.state, "oauth_provider", None)
        if provider is None:
            return None

        record = await provider.load_access_token(api_key)
        if record is None:
            raise UnauthenticatedError(
                "OAuth access token is invalid, expired, or revoked"
            )

        import hmac

        api_key_manager = getattr(request.app.state, "api_key_manager", None)
        recorded_fp = record.authorizing_key_fp
        current_fp: Optional[str] = None
        if api_key_manager is not None and hasattr(
            api_key_manager, "get_user_key_fingerprint"
        ):
            current_fp = api_key_manager.get_user_key_fingerprint(
                record.account_id, record.user_id
            )
        if not recorded_fp or not current_fp or not hmac.compare_digest(
            recorded_fp, current_fp
        ):
            raise UnauthenticatedError(
                "OAuth token's authorizing API key has been rotated or revoked; "
                "please re-authorize the client."
            )

        role = Role(record.role)

        # Role downgrade protection
        if api_key_manager is not None and hasattr(api_key_manager, "get_user_role"):
            try:
                current_role = Role(api_key_manager.get_user_role(
                    record.account_id, record.user_id
                ))
            except Exception:
                raise UnauthenticatedError(
                    "OAuth token validation failed: unable to verify user's current role; "
                    "please re-authorize the client."
                )
            if role.rank > current_role.rank:
                raise UnauthenticatedError(
                    "OAuth token's embedded role exceeds the user's current role; "
                    "please re-authorize the client."
                )

        # Silently ignore identity assertion headers in api_key mode.
        if x_openviking_account:
            _remove_header(request, b"x-openviking-account")
        if x_openviking_user:
            _remove_header(request, b"x-openviking-user")

        return ResolvedIdentity(
            role=role,
            account_id=record.account_id,
            user_id=record.user_id,
            from_oauth=True,
        )

    def validate_config(self, config) -> None:
        if config.root_api_key and config.root_api_key != "":
            return
        import logging

        logger = logging.getLogger(__name__)
        if _is_localhost(config.host):
            logger.error(
                "server.auth_mode='api_key' requires server.root_api_key to be configured.\n"
                'To run without authentication on localhost, either set server.auth_mode="dev" '
                "or simply remove the server.auth_mode setting to auto-detect."
            )
        else:
            logger.error(
                "SECURITY: server.auth_mode='api_key' requires server.root_api_key "
                "to be configured when server.host is '%s' (non-localhost).",
                config.host,
            )
        logger.error(
            "To fix, either:\n"
            "  1. Set server.root_api_key in ov.conf, or\n"
            '  2. Use server.auth_mode="dev" (localhost only)'
        )
        sys.exit(1)

    async def initialize(self, app, service, config) -> None:
        if not config.root_api_key or config.root_api_key == "":
            raise RuntimeError("api_key mode requires root_api_key")
        api_key_manager = APIKeyManager(
            root_key=config.root_api_key,
            viking_fs=service.viking_fs,
            api_key_hashing_enabled=config.api_key_hashing_enabled,
        )
        await api_key_manager.load()
        app.state.api_key_manager = api_key_manager

    def get_request_context_checks(
        self,
        path: str,
        identity: ResolvedIdentity,
    ) -> None:
        if (
            identity.role == Role.ROOT
            and not identity.from_oauth
            and not _api_key_root_can_access_path(path)
        ):
            raise PermissionDeniedError(
                "ROOT API keys cannot access tenant-scoped data APIs in api_key mode. "
                "Use a user/admin API key for data access, or trusted mode for upstream "
                "identity assertion."
            )


def _is_localhost(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}
