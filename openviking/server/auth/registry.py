# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Auth plugin registry for OpenViking multi-tenant HTTP Server."""

from __future__ import annotations

from typing import Optional, TypeVar

from openviking.server.auth.plugin import AuthPlugin
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=AuthPlugin)


class AuthPluginRegistry:
    """Registry for authentication plugins.

    Supports decorator-based registration, programmatic registration, and
    lookup by ``auth_mode`` string.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, type[AuthPlugin]] = {}

    def register(self, plugin_class: type[T]) -> type[T]:
        """Register an auth plugin class.

        Supports decorator syntax:

            @registry.register
            class MyAuthPlugin(AuthPlugin):
                auth_mode = "custom"
                ...

        Args:
            plugin_class: Concrete AuthPlugin subclass.

        Returns:
            The registered class (for decorator chaining).

        Raises:
            ValueError: If ``auth_mode`` is empty or already registered.
        """
        mode = plugin_class.auth_mode
        if not mode:
            raise ValueError(
                f"AuthPlugin {plugin_class.__name__} must define 'auth_mode'"
            )
        if mode in self._plugins:
            existing = self._plugins[mode].__name__
            raise ValueError(
                f"Auth mode {mode!r} is already registered by {existing}"
            )
        self._plugins[mode] = plugin_class
        logger.info("Registered auth plugin: %s (%s)", mode, plugin_class.__name__)
        return plugin_class

    def get(self, mode: str) -> Optional[type[AuthPlugin]]:
        """Get plugin class by auth mode string."""
        return self._plugins.get(mode)

    def list_modes(self) -> list[str]:
        """List all registered auth modes."""
        return list(self._plugins.keys())


# Global registry instance ---------------------------------------------------

_global_registry = AuthPluginRegistry()


def get_registry() -> AuthPluginRegistry:
    """Get the global auth plugin registry."""
    return _global_registry


def register_auth_plugin(plugin_class: type[T]) -> type[T]:
    """Decorator to register an auth plugin in the global registry.

    Usage::

        from openviking.server.auth.registry import register_auth_plugin

        @register_auth_plugin
        class LDAPAuthPlugin(AuthPlugin):
            auth_mode = "ldap"
            ...
    """
    return _global_registry.register(plugin_class)
