# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage event sinks."""

from __future__ import annotations

from typing import Protocol

from .models import UsageEvent


class UsageSink(Protocol):
    async def write(self, *, events: list[UsageEvent]) -> None: ...
