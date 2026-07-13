# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage event sinks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import UsageContext, UsageEvent


class UsageSink(Protocol):
    async def write(self, *, events: list[UsageEvent], context: UsageContext) -> None: ...


class FileJsonlUsageSink:
    """Append usage events to a local JSONL file.

    This sink is intentionally small and dependency-free so it can be used by
    tests and local deployments without pulling in external message systems.
    """

    def __init__(self, *, path: str):
        self.path = Path(path)

    async def write(self, *, events: list[UsageEvent], context: UsageContext) -> None:
        del context
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
                f.write("\n")
