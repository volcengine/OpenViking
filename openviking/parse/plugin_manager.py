# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Discovery and loading helpers for parser providers."""

import importlib
import logging
import pkgutil
from types import ModuleType
from typing import Dict, Mapping, Optional

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.plugin_base import ParserProvider
from openviking_cli.utils.config.parser_config import ParserConfig

logger = logging.getLogger(__name__)


class ParserPluginManager:
    """Discover parser providers from an in-package plugin directory."""

    def __init__(
        self,
        plugin_package: str = "openviking.parse.plugins",
        parser_configs: Optional[Mapping[str, ParserConfig]] = None,
    ):
        self._plugin_package = plugin_package
        self._parser_configs = dict(parser_configs or {})
        self._providers: Optional[Dict[str, ParserProvider]] = None

    def discover_providers(self) -> Dict[str, ParserProvider]:
        """Discover providers exposed by the configured plugin package."""
        if self._providers is not None:
            return dict(self._providers)

        discovered: Dict[str, ParserProvider] = {}

        try:
            package = importlib.import_module(self._plugin_package)
        except Exception:
            logger.warning("Failed to import parser plugin package %s", self._plugin_package)
            self._providers = discovered
            return dict(discovered)

        package_path = getattr(package, "__path__", None)
        if package_path is None:
            self._providers = discovered
            return dict(discovered)

        for module_info in pkgutil.iter_modules(package_path, f"{package.__name__}."):
            provider = self._load_provider_from_module(module_info.name)
            if provider is None:
                continue
            discovered[provider.name] = provider

        self._providers = discovered
        return dict(discovered)

    def get_provider(self, name: str) -> Optional[ParserProvider]:
        """Return a discovered provider by name."""
        return self.discover_providers().get(name)

    def is_provider_available(self, name: str) -> bool:
        """Return whether a discovered provider is available for use."""
        provider = self.get_provider(name)
        return bool(provider and provider.is_available())

    def list_available_providers(self) -> list[str]:
        """List names of available providers."""
        return sorted(
            name
            for name, provider in self.discover_providers().items()
            if provider.is_available()
        )

    def create_parser(self, name: str) -> Optional[BaseParser]:
        """Create a parser from a discovered provider."""
        provider = self.get_provider(name)
        if provider is None or not provider.is_available():
            return None
        return provider.create_parser(config=self._parser_configs.get(name))

    def _load_provider_from_module(self, module_name: str) -> Optional[ParserProvider]:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.warning("Failed to import parser provider module %s", module_name, exc_info=True)
            return None

        candidate = self._resolve_provider_candidate(module)
        if candidate is None:
            return None
        if isinstance(candidate, type):
            candidate = candidate()
        if not isinstance(candidate, ParserProvider):
            logger.warning(
                "Skipping parser provider module %s: unsupported provider object %r",
                module_name,
                candidate,
            )
            return None
        return candidate

    @staticmethod
    def _resolve_provider_candidate(module: ModuleType) -> Optional[object]:
        if hasattr(module, "PROVIDER"):
            return getattr(module, "PROVIDER")
        if hasattr(module, "get_provider"):
            return module.get_provider()
        return None
