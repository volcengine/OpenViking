# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Packaged system assets shipped with OpenViking."""

from pathlib import Path

from openviking.core.namespace_constants import SYSTEM_SEMANTIC_BUILTIN_PATH

SYSTEM_ASSETS_DIR = Path(__file__).parent
BUILTIN_SEMANTIC_TEMPLATES_DIR = SYSTEM_ASSETS_DIR / SYSTEM_SEMANTIC_BUILTIN_PATH


def builtin_memory_templates_dir() -> Path:
    """Return the packaged builtin memory template directory."""
    return SYSTEM_ASSETS_DIR / "memory" / "memory_templates" / "builtin"
