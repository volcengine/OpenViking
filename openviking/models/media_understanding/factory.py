# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Factory for media understanding providers."""

from collections.abc import Mapping
from typing import Any

from .base import MediaUnderstandingClient


class MediaUnderstandingFactory:
    """Create media understanding clients from configuration mappings."""

    @staticmethod
    def create(config: Mapping[str, Any]) -> MediaUnderstandingClient:
        provider = str(config.get("provider", "")).strip().lower()
        if provider != "volcengine":
            raise ValueError(f"Unsupported media understanding provider: {provider}")

        from .backends.volcengine import VolcengineMediaUnderstandingClient

        return VolcengineMediaUnderstandingClient(dict(config))
