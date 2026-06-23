# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Built-in authentication plugins for OpenViking."""

from openviking.server.auth.plugins.api_key import ApiKeyAuthPlugin
from openviking.server.auth.plugins.dev import DevAuthPlugin
from openviking.server.auth.plugins.trusted import TrustedAuthPlugin
from openviking.server.auth.registry import get_registry

# Register built-in plugins on import
_registry = get_registry()
_registry.register(DevAuthPlugin)
_registry.register(ApiKeyAuthPlugin)
_registry.register(TrustedAuthPlugin)

__all__ = [
    "DevAuthPlugin",
    "ApiKeyAuthPlugin",
    "TrustedAuthPlugin",
]
