# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Trusted mode authentication plugin."""

from __future__ import annotations

import hmac
import sys
from typing import Optional

from fastapi import Request

from openviking.server.api_keys import APIKeyManager
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import InvalidArgumentError, UnauthenticatedError

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}
_TRUSTED_RELAXED_IDENTITY_PREFIXES = ("/api/v1/admin",)


def _is_localhost(host: str) -> bool:
    return host in _LOCALHOST_HOSTS


def _configured_root_api_key(request: Request) -> Optional[str]:
    config = getattr(request.app.state, "config", None)
    key = getattr(config, "root_api_key", None)
    return key if key != "" else None


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


def _trusted_request_requires_explicit_identity(path: str) -> bool:
    if path.startswith(_TRUSTED_RELAXED_IDENTITY_PREFIXES):
        return False
    return True


class TrustedAuthPlugin(AuthPlugin):
    """Trusted mode: trust X-OpenViking-Account/User headers.

    Optionally validates a configured root_api_key. Role is looked up from
    APIKeyManager if the user exists, otherwise defaults to USER.
    """

    auth_mode = "trusted"

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
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

        # Admin paths may omit identity completely, but partial identity is rejected.
        is_admin_path = request.url.path.startswith(_TRUSTED_RELAXED_IDENTITY_PREFIXES)
        if is_admin_path:
            if bool(effective_account_id) != bool(effective_user_id):
                raise InvalidArgumentError(
                    "Trusted mode requests must include "
                    "X-OpenViking-Account or explicit account_id in the URL and "
                    "X-OpenViking-User or explicit user_id in the URL."
                )
            if configured_root_api_key:
                return ResolvedIdentity(
                    role=Role.ROOT,
                    account_id=effective_account_id or "trusted",
                    user_id=effective_user_id or "trusted",
                )
            if not effective_account_id and not effective_user_id:
                return ResolvedIdentity(
                    role=Role.ROOT,
                    account_id="trusted",
                    user_id="trusted",
                )

        if _trusted_request_requires_explicit_identity(request.url.path):
            missing_fields = []
            if not effective_account_id:
                missing_fields.append(
                    "X-OpenViking-Account or explicit account_id in the URL"
                )
            if not effective_user_id:
                missing_fields.append(
                    "X-OpenViking-User or explicit user_id in the URL"
                )
            if missing_fields:
                raise InvalidArgumentError(
                    "Trusted mode requests must include " + " and ".join(missing_fields) + "."
                )

        api_key_manager = getattr(request.app.state, "api_key_manager", None)
        trusted_role = Role.USER
        if api_key_manager and effective_account_id and effective_user_id:
            looked_up_role = api_key_manager.get_user_role(
                effective_account_id, effective_user_id
            )
            if looked_up_role is not None:
                trusted_role = looked_up_role

        return ResolvedIdentity(
            role=trusted_role,
            account_id=effective_account_id or "trusted",
            user_id=effective_user_id or "trusted",
        )

    def validate_config(self, config) -> None:
        if config.root_api_key and config.root_api_key != "":
            return
        if _is_localhost(config.host):
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "Trusted mode without API key: authentication trusts "
                "X-OpenViking-Account/User headers. This is allowed because "
                "the server is bound to localhost (%s).",
                config.host,
            )
            return
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            "SECURITY: server.auth_mode='trusted' requires server.root_api_key when "
            "server.host is '%s' (non-localhost). Only localhost trusted mode may run "
            "without an API key.",
            config.host,
        )
        logger.error(
            "To fix, either:\n"
            "  1. Set server.root_api_key in ov.conf, or\n"
            '  2. Bind trusted mode to localhost (server.host = "127.0.0.1")'
        )
        sys.exit(1)

    async def initialize(self, app, service, config) -> None:
        if config.root_api_key and config.root_api_key != "":
            api_key_manager = APIKeyManager(
                root_key=config.root_api_key,
                viking_fs=service.viking_fs,
                api_key_hashing_enabled=config.api_key_hashing_enabled,
            )
            await api_key_manager.load()
            app.state.api_key_manager = api_key_manager
        else:
            app.state.api_key_manager = None

    def requires_api_key_manager(self) -> bool:
        return False

    def get_request_context_checks(
        self,
        path: str,
        identity: ResolvedIdentity,
    ) -> None:
        is_admin_path = path.startswith(_TRUSTED_RELAXED_IDENTITY_PREFIXES)
        if not is_admin_path:
            if not identity.account_id:
                raise InvalidArgumentError(
                    "Trusted mode requests must include X-OpenViking-Account."
                )
            if not identity.user_id:
                raise InvalidArgumentError(
                    "Trusted mode requests must include X-OpenViking-User."
                )
