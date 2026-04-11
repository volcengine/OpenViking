# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for loading and registering custom parsers from ``ov.conf``."""

import importlib
import json
import logging
from pathlib import Path
from typing import Optional

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.registry import ParserRegistry, get_registry
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig, get_openviking_config

logger = logging.getLogger(__name__)

_REGISTRATION_KEY_ATTR = "_custom_parser_registration_key"
_REGISTERED_NAMES_ATTR = "_custom_parser_registered_names"


class ConfiguredParserWrapper(BaseParser):
    """Delegate parser behavior while overriding supported extensions."""

    def __init__(self, parser: BaseParser, extensions: list[str]):
        self.parser = parser
        self._extensions = list(extensions)

    @property
    def supported_extensions(self) -> list[str]:
        return list(self._extensions)

    async def parse(self, source, instruction: str = "", **kwargs):
        return await self.parser.parse(source, instruction=instruction, **kwargs)

    async def parse_content(
        self, content, source_path: Optional[str] = None, instruction="", **kwargs
    ):
        return await self.parser.parse_content(
            content,
            source_path=source_path,
            instruction=instruction,
            **kwargs,
        )

    def can_parse(self, path):
        return Path(path).suffix.lower() in self._extensions

    def __getattr__(self, name: str):
        return getattr(self.parser, name)


def _load_parser_class(class_path: str) -> type[BaseParser]:
    try:
        module_name, class_name = class_path.rsplit(".", 1)
    except ValueError as exc:
        raise ImportError(f"Invalid custom parser class path: {class_path}") from exc

    module = importlib.import_module(module_name)
    try:
        parser_class = getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(f"Could not import custom parser class '{class_path}'") from exc
    if not isinstance(parser_class, type) or not issubclass(parser_class, BaseParser):
        raise TypeError(f"Custom parser class '{class_path}' must inherit from BaseParser")
    return parser_class


def build_custom_parser_registration_key(config: OpenVikingConfig) -> str:
    payload = {
        name: parser_config.model_dump(by_alias=True)
        for name, parser_config in config.custom_parsers.items()
    }
    return json.dumps(payload, sort_keys=True)


def register_configured_custom_parsers(
    *,
    registry: Optional[ParserRegistry] = None,
    config: Optional[OpenVikingConfig] = None,
) -> ParserRegistry:
    """Register configured custom parsers onto the target registry."""

    resolved_registry = registry or get_registry()
    resolved_config = config or get_openviking_config()
    registration_key = build_custom_parser_registration_key(resolved_config)

    if getattr(resolved_registry, _REGISTRATION_KEY_ATTR, None) == registration_key:
        return resolved_registry

    for name in getattr(resolved_registry, _REGISTERED_NAMES_ATTR, ()):
        resolved_registry.unregister(name)

    registered_names: list[str] = []
    for name, parser_config in resolved_config.custom_parsers.items():
        parser_class = _load_parser_class(parser_config.class_path)
        try:
            parser = parser_class(**parser_config.kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to instantiate custom parser '{name}' "
                f"from '{parser_config.class_path}': {exc}"
            ) from exc

        wrapped = ConfiguredParserWrapper(parser=parser, extensions=parser_config.extensions)
        resolved_registry.register(name, wrapped)
        registered_names.append(name)
        logger.info(
            "Registered custom parser '%s' from %s for %s",
            name,
            parser_config.class_path,
            parser_config.extensions,
        )

    setattr(resolved_registry, _REGISTERED_NAMES_ATTR, tuple(registered_names))
    setattr(resolved_registry, _REGISTRATION_KEY_ATTR, registration_key)
    return resolved_registry
