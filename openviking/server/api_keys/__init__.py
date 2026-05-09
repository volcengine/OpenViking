# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unified API Key management with support for both legacy and new formats.

This module exports APIKeyManager which internally uses NewAPIKeyManager that:
- Generates new keys in the new format: base64url(account_id).base64url(user_id).base64url(secret)
- Can resolve both legacy and new format keys
- Maintains full backward compatibility
"""

# Also export legacy components for reference
from openviking.server.api_keys.legacy import LegacyAPIKeyManager
from openviking.server.api_keys.new import (
    NewAPIKeyManager as APIKeyManager,
)
from openviking.server.api_keys.new import (
    generate_api_key,
    is_new_format_key,
    parse_api_key,
)

__all__ = [
    "APIKeyManager",
    "LegacyAPIKeyManager",
    "is_new_format_key",
    "parse_api_key",
    "generate_api_key",
]
