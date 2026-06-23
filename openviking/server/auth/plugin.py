# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Auth plugin abstract base class for OpenViking multi-tenant HTTP Server."""

from __future__ import annotations

import abc
import sys
from typing import TYPE_CHECKING, ClassVar, Optional

from fastapi import Request

from openviking.server.identity import ResolvedIdentity
from openviking_cli.exceptions import UnauthenticatedError

if TYPE_CHECKING:
    from openviking.server.config import ServerConfig
    from openviking.service.core import OpenVikingService


class AuthPlugin(abc.ABC):
    """Authentication plugin abstract base class.

    Each ``auth_mode`` value maps to one plugin implementation.
    Third-party plugins subclass this and register via
    ``@register_auth_plugin`` or ``registry.register()``.
    """

    # Subclasses MUST define this, e.g. auth_mode = "ldap"
    auth_mode: ClassVar[str] = ""

    @abc.abstractmethod
    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """Resolve identity from request credentials.

        Args:
            request: FastAPI request object.
            api_key: Extracted API key from X-API-Key or Authorization header.
            x_openviking_account: Normalized X-OpenViking-Account header value.
            x_openviking_user: Normalized X-OpenViking-User header value.

        Returns:
            ResolvedIdentity with role and user identification.

        Raises:
            UnauthenticatedError: When authentication fails.
            InvalidArgumentError: When request is malformed.
            PermissionDeniedError: When identity assertion is not allowed.
        """
        ...

    @abc.abstractmethod
    def validate_config(self, config: ServerConfig) -> None:
        """Validate server configuration for this plugin.

        Called during server startup. Should exit the process (sys.exit) on
        fatal misconfiguration.

        Args:
            config: ServerConfig instance to validate.
        """
        ...

    @abc.abstractmethod
    async def initialize(
        self,
        app,
        service: OpenVikingService,
        config: ServerConfig,
    ) -> None:
        """Initialize plugin during app startup.

        Called after service initialization but before serving traffic.
        Plugins may store runtime state (e.g. APIKeyManager) on ``app.state``.

        Args:
            app: FastAPI application instance.
            service: OpenVikingService instance.
            config: ServerConfig instance.
        """
        ...

    def get_request_context_checks(
        self,
        path: str,
        identity: ResolvedIdentity,
    ) -> None:
        """Additional path/identity checks after resolving identity.

        Called by ``get_request_context()`` after ``resolve_identity()``
        succeeds. Should raise appropriate exceptions if checks fail.

        Args:
            path: Request URL path.
            identity: Resolved identity from ``resolve_identity()``.

        Raises:
            InvalidArgumentError: When identity is missing required fields.
            PermissionDeniedError: When access is denied to the path.
        """
        pass

    def requires_api_key_manager(self) -> bool:
        """Whether Admin API routes require an APIKeyManager in this mode.

        Returns:
            True if ``api_key_manager`` must be present for admin routes.
        """
        return True

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        """Whether the bot proxy may skip API key validation.

        Returns:
            True if the bot proxy can forward without an API key
            (e.g. dev mode).
        """
        return False
