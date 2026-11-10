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

# Try to register OIDC and LDAP plugins if dependencies are available
try:
    from openviking.server.auth.plugins.oidc import OIDCAuthPlugin
    _registry.register(OIDCAuthPlugin)
    _has_oidc = True
except ImportError:
    _has_oidc = False

try:
    from openviking.server.auth.plugins.ldap import LDAPAuthPlugin
    _registry.register(LDAPAuthPlugin)
    _has_ldap = True
except ImportError:
    _has_ldap = False

__all__ = [
    "DevAuthPlugin",
    "ApiKeyAuthPlugin",
    "TrustedAuthPlugin",
]

if _has_oidc:
    __all__.append("OIDCAuthPlugin")
if _has_ldap:
    __all__.append("LDAPAuthPlugin")
