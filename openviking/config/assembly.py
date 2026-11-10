# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Startup assembly for the runtime config system.

Service startup calls the binding layer, which constructs a source through this
module and injects it into the manager. Source selection is read from ``ov.conf``
and cannot be changed through the runtime PATCH API::

    {
      "runtime_config": {
        "source": "file"
      }
    }

``params`` is passed unchanged to external providers. The built-in ``file``
source is constructed only by the kernel binding layer, which owns its AGFS
client and never exposes it to plugin factories.
"""

from __future__ import annotations

from openviking.config.source.base import ConfigSource, ConfigSourceContext
from openviking.config.source.registry import create_config_source, load_config_source_module
from openviking_cli.utils.config.open_viking_config import RuntimeConfigSettings


def build_config_source(
    settings: RuntimeConfigSettings | dict | None,
) -> ConfigSource:
    """Construct an external configured :class:`ConfigSource`.

    ``settings`` may be a :class:`RuntimeConfigSettings`, a plain dict from
    ``ov.conf``, or ``None`` (which resolves to the kernel-owned ``file``
    source). External providers receive only ``params``.
    """
    resolved = resolve_config_source_settings(settings)
    if resolved.source.strip().lower() == "file":
        raise ValueError("The built-in 'file' config source is kernel-owned")
    if resolved.module:
        load_config_source_module(resolved.module)
    ctx = ConfigSourceContext(params=dict(resolved.params))
    return create_config_source(resolved.source, ctx)


def resolve_config_source_settings(
    settings: RuntimeConfigSettings | dict | None,
) -> RuntimeConfigSettings:
    """Normalize boot configuration before selecting a kernel or plugin source."""
    if settings is None:
        return RuntimeConfigSettings()
    if isinstance(settings, RuntimeConfigSettings):
        return settings
    if isinstance(settings, dict):
        return RuntimeConfigSettings(**settings)
    raise TypeError(f"unsupported runtime_config settings type: {type(settings)!r}")
