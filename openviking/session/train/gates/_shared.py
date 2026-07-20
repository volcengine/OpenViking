# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared implementation helpers for active policy training gates."""

from __future__ import annotations


def _preview_text(text: str, *, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."
