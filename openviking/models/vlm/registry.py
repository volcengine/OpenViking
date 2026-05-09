# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Provider Registry — single source of truth for canonical LLM provider metadata.
"""

from __future__ import annotations

VALID_PROVIDERS: tuple[str, ...] = (
    "volcengine",
    "openai",
    "azure",
    "kimi",
    "glm",
    "litellm",
    "openai-codex",
)

DEFAULT_AZURE_API_VERSION: str = "2025-01-01-preview"


def get_all_provider_names() -> list[str]:
    """Get all provider names list."""
    return list(VALID_PROVIDERS)


def is_valid_provider(name: str) -> bool:
    """Check if provider name is valid."""
    return name.lower() in VALID_PROVIDERS
