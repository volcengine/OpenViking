# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""External config source registry.

Plugins register by name through the ``openviking.config_source`` entry-point
group or an ``ov.conf`` module path. Kernel-owned sources, such as the AGFS
file source, are deliberately not registrable here.
"""

from __future__ import annotations

import importlib
from typing import Callable

from openviking.config.source.base import ConfigSource, ConfigSourceContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

ConfigSourceFactory = Callable[[ConfigSourceContext], ConfigSource]

_FACTORIES: dict[str, ConfigSourceFactory] = {}

ENTRY_POINT_GROUP = "openviking.config_source"


def register_config_source(name: str) -> Callable[[ConfigSourceFactory], ConfigSourceFactory]:
    """Decorator registering a source factory under ``name``."""
    normalized = _normalize(name)

    def deco(factory: ConfigSourceFactory) -> ConfigSourceFactory:
        if normalized in _FACTORIES:
            existing = getattr(_FACTORIES[normalized], "__name__", "?")
            raise ValueError(f"config source {normalized!r} is already registered by {existing}")
        _FACTORIES[normalized] = factory
        logger.debug("Registered config source: %s", normalized)
        return factory

    return deco


def create_config_source(name: str, ctx: ConfigSourceContext) -> ConfigSource:
    """Construct a source by name, importing plugins on demand.

    Unknown names raise ``ValueError`` after trying entry points.
    """
    normalized = _normalize(name)
    if normalized == "memory":
        importlib.import_module(f"openviking.config.source.{normalized}_source")
    if normalized not in _FACTORIES:
        _load_from_entry_points(normalized)
    factory = _FACTORIES.get(normalized)
    if factory is None:
        available = ", ".join(sorted(_FACTORIES)) or "<none>"
        raise ValueError(
            f"Unknown config source {name!r}. Available: {available}. "
            "Install a plugin exposing it via the 'openviking.config_source' "
            "entry point or set runtime_config.module."
        )
    return factory(ctx)


def load_config_source_module(module_path: str) -> None:
    """Import an ``ov.conf``-specified module so it can self-register."""
    importlib.import_module(module_path)


def _load_from_entry_points(name: str) -> None:
    from importlib.metadata import entry_points

    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        if _normalize(ep.name) == name:
            factory = ep.load()
            if name not in _FACTORIES:
                register_config_source(name)(factory)
            return


def _normalize(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("config source name must not be empty")
    return normalized
