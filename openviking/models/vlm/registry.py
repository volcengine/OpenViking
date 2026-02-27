# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Provider Registry — single source of truth for LLM provider metadata.

Supported providers: volcengine, openai, litellm
"""

from __future__ import annotations

VALID_PROVIDERS: tuple[str, ...] = ("volcengine", "openai", "litellm")


def get_all_provider_names() -> list[str]:
    """Get all provider names list."""
    return list(VALID_PROVIDERS)


def is_valid_provider(name: str) -> bool:
    """Check if provider name is valid."""
    return name.lower() in VALID_PROVIDERS
