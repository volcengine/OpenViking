# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""API key management facade over complete account stores."""

from openviking.server.api_keys.new import (
    APIKeyManager,
    generate_api_key,
    is_new_format_key,
    parse_api_key,
)

__all__ = [
    "APIKeyManager",
    "is_new_format_key",
    "parse_api_key",
    "generate_api_key",
]
