# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dev mode authentication plugin."""

from __future__ import annotations

import sys
from typing import Optional

from fastapi import Request

from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import InvalidArgumentError

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_localhost(host: str) -> bool:
    """Return True if *host* resolves to a loopback address."""
    return host in _LOCALHOST_HOSTS


class DevAuthPlugin(AuthPlugin):
    """Development mode: no authentication, always ROOT.

    Only allowed when the server binds to localhost.
    """

    auth_mode = "dev"

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """Dev mode: no authentication, always return ROOT."""
        return ResolvedIdentity(
            role=Role.ROOT,
            account_id=x_openviking_account or "default",
            user_id=x_openviking_user or "default",
        )

    def validate_config(self, config) -> None:
        """Dev mode is only allowed on localhost."""
        if _is_localhost(config.host):
            return
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            "SECURITY: server.auth_mode='dev' requires server.host to be localhost, "
            "but it is set to '%s'. Dev mode exposes an unauthenticated ROOT "
            "endpoint and must not be exposed to the network.",
            config.host,
        )
        logger.error(
            "To fix, either:\n"
            '  1. Set server.auth_mode="api_key" and configure server.root_api_key, or\n'
            '  2. Bind dev mode to localhost (server.host = "127.0.0.1")'
        )
        sys.exit(1)

    async def initialize(self, app, service, config) -> None:
        """Dev mode does not need an APIKeyManager."""
        app.state.api_key_manager = None

    def requires_api_key_manager(self) -> bool:
        return False

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        return True
