# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Config source abstraction, registry, and built-in implementations.

A :class:`ConfigSource` owns *where an override comes from, how it is written
back, how change is detected, and whether/how it is encrypted*. The
:class:`~openviking.config.manager.RuntimeConfigManager` owns merge, publish and
invalidation and depends only on this abstraction.
"""

from openviking.config.source.base import (
    ConfigSource,
    ConfigSourceContext,
    Mutate,
)
from openviking.config.source.registry import (
    create_config_source,
    register_config_source,
)

__all__ = [
    "ConfigSource",
    "ConfigSourceContext",
    "Mutate",
    "FileConfigSource",
    "MemoryConfigSource",
    "create_config_source",
    "register_config_source",
]


def __getattr__(name):
    if name == "FileConfigSource":
        from openviking.config.source.file_source import FileConfigSource

        return FileConfigSource
    if name == "MemoryConfigSource":
        from openviking.config.source.memory_source import MemoryConfigSource

        return MemoryConfigSource
    raise AttributeError(name)
