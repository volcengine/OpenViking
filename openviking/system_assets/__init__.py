# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Packaged system assets shipped with OpenViking."""

from pathlib import Path

SYSTEM_ASSETS_DIR = Path(__file__).parent


def builtin_memory_templates_dir() -> Path:
    """Return the packaged builtin memory template directory."""
    return SYSTEM_ASSETS_DIR / "memory" / "memory_templates" / "builtin"
