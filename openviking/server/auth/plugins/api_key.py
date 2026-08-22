# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""API key mode authentication plugin."""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from fastapi import Request

from openviking.server.api_keys import APIKeyManager
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import PermissionDeniedError, UnauthenticatedError
from openviking_cli.utils import get_logger

_watch_logger = get_logger(__name__)

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


def _reject_identity_assertion_headers(
    x_openviking_account: Optional[str],
    x_openviking_user: Optional[str],
) -> None:
    """Reject caller-controlled tenant assertions in key-authenticated mode.

    The account and user headers are assertions, not metadata.  Silently
    dropping them made malformed or compromised clients appear successful and
    allowed callers to confuse the request they signed with the request the
    server authorized.  Trusted mode is the explicit opt-in path for these
    headers; API-key and OAuth identities are bound to their credential.
    """
    if x_openviking_account:
        raise PermissionDeniedError(
            "X-OpenViking-Account cannot override an API-key or OAuth identity."
        )
    if x_openviking_user:
        raise PermissionDeniedError(
            "X-OpenViking-User cannot override an API-key or OAuth identity."
        )


class ApiKeyAuthPlugin(AuthPlugin):
    """API key mode: resolve identity via APIKeyManager.

    Supports OAuth bearer tokens layered on top of API keys.
    """

    auth_mode = "api_key"

    def __init__(self) -> None:
        # Background key-store poll task; None when the watcher is disabled.
        self._watch_task: Optional[asyncio.Task] = None

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

        _reject_identity_assertion_headers(x_openviking_account, x_openviking_user)

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
            raise UnauthenticatedError("OAuth access token is invalid, expired, or revoked")

        import hmac

        api_key_manager = getattr(request.app.state, "api_key_manager", None)
        recorded_fp = record.authorizing_key_fp
        current_fp: Optional[str] = None
        if api_key_manager is not None and hasattr(api_key_manager, "get_user_key_fingerprint"):
            current_fp = api_key_manager.get_user_key_fingerprint(record.account_id, record.user_id)
        if not recorded_fp or not current_fp or not hmac.compare_digest(recorded_fp, current_fp):
            raise UnauthenticatedError(
                "OAuth token's authorizing API key has been rotated or revoked; "
                "please re-authorize the client."
            )

        role = Role(record.role)

        # Role downgrade protection
        if api_key_manager is not None and hasattr(api_key_manager, "get_user_role"):
            try:
                current_role = Role(
                    api_key_manager.get_user_role(record.account_id, record.user_id)
                )
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

        _reject_identity_assertion_headers(x_openviking_account, x_openviking_user)

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

        # Read replicas never rewrite the store, so poll for writer-side changes.
        if getattr(config, "api_key_watch_enabled", False):
            interval = getattr(config, "api_key_watch_interval_seconds", 30.0)
            self._watch_task = asyncio.create_task(self._watch_key_store(api_key_manager, interval))
            _watch_logger.info("API key store watcher started (interval=%.1fs)", interval)

    async def shutdown(self) -> None:
        """Cancel the background key-store watcher, if running."""
        if self._watch_task is None:
            return
        self._watch_task.cancel()
        try:
            await self._watch_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            _watch_logger.warning("API key store watcher exited with error", exc_info=True)
        finally:
            self._watch_task = None

    @staticmethod
    async def _watch_key_store(api_key_manager: APIKeyManager, interval: float) -> None:
        """Poll the key store and reload on signature change; errors are swallowed to survive."""
        # Guard against a misconfigured non-positive interval spinning hot.
        interval = interval if interval and interval > 0 else 30.0
        try:
            last_signature = await api_key_manager.compute_store_signature()
        except Exception:  # noqa: BLE001
            _watch_logger.warning(
                "Initial key-store signature failed; watcher will retry", exc_info=True
            )
            last_signature = None

        while True:
            await asyncio.sleep(interval)
            try:
                signature = await api_key_manager.compute_store_signature()
            except Exception:  # noqa: BLE001
                _watch_logger.debug("Key-store signature check failed", exc_info=True)
                continue

            if signature == last_signature:
                continue

            try:
                await api_key_manager.reload()
                last_signature = signature
                _watch_logger.info("API key store change detected; in-memory index reloaded")
            except Exception:  # noqa: BLE001
                # Keep last_signature unchanged so we retry the reload next tick.
                _watch_logger.warning("API key store reload failed; will retry", exc_info=True)

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
