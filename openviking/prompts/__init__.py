# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Prompt template management for OpenViking."""

from .manager import get_llm_config, get_manager, render_prompt

__all__ = ["render_prompt", "get_llm_config", "get_manager"]
