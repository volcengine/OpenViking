# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for handling multimodal request data safely."""

from __future__ import annotations

from typing import Any


def redact_image_data_urls(value: Any) -> Any:
    """Return a copy with inline image payloads replaced by compact placeholders."""
    if isinstance(value, str):
        if value.lower().startswith("data:image/") and ";base64," in value.lower():
            header, payload = value.split(",", 1)
            return f"{header},[redacted {len(payload)} base64 chars]"
        return value
    if isinstance(value, list):
        return [redact_image_data_urls(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_image_data_urls(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_image_data_urls(item) for key, item in value.items()}
    return value
