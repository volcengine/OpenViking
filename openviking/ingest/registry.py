# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Registry of harness log-source adapters.

Adding a harness = one ``@register_source("name")`` decorator on a ``LogSource``
subclass; no config-schema change is needed (config keys are free-form).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterator, Tuple, Type

from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.ingest.sources.base import LogSource
    from openviking_cli.utils.config.ingest_config import IngestConfig, IngestHarnessConfig

logger = get_logger(__name__)

SOURCE_REGISTRY: Dict[str, "Type[LogSource]"] = {}


def register_source(name: str):
    """Class decorator registering a ``LogSource`` subclass under ``name``."""

    def deco(cls: "Type[LogSource]") -> "Type[LogSource]":
        cls.name = name
        if name in SOURCE_REGISTRY and SOURCE_REGISTRY[name] is not cls:
            logger.warning("[ingest] source %r already registered; overriding", name)
        SOURCE_REGISTRY[name] = cls
        return cls

    return deco


def get_source_class(name: str) -> "Type[LogSource] | None":
    # Ensure built-in adapters have been imported (and thus registered).
    import openviking.ingest.sources  # noqa: F401

    return SOURCE_REGISTRY.get(name)


def iter_enabled_sources(
    config: "IngestConfig",
) -> Iterator[Tuple[str, "IngestHarnessConfig", "LogSource"]]:
    """Yield ``(name, harness_cfg, source)`` for each enabled, registered harness."""
    import openviking.ingest.sources  # noqa: F401  (populates SOURCE_REGISTRY)

    for name, harness_cfg in config.enabled_harnesses().items():
        cls = SOURCE_REGISTRY.get(name)
        if cls is None:
            logger.warning("[ingest] no adapter registered for harness %r; skipping", name)
            continue
        yield name, harness_cfg, cls(harness_cfg, fallback_user=config.user)
