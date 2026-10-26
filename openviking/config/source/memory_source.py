# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""In-memory config source for tests and explicit volatile mode."""

from __future__ import annotations

import copy
from typing import Optional

from openviking.concurrency import AsyncSemaphore
from openviking.config.scope import ConfigScope
from openviking.config.source.base import ConfigSourceContext, Mutate
from openviking.config.source.registry import register_config_source


class MemoryConfigSource:
    """Volatile source keeping overrides in a process-local dict."""

    def __init__(self, ctx: Optional[ConfigSourceContext] = None) -> None:
        self._namespace = ctx.params.get("namespace", "") if ctx else ""
        self._store: dict[tuple, Optional[dict]] = {}
        self._lock = AsyncSemaphore()

    def _key(self, scope: ConfigScope) -> tuple:
        return (self._namespace, scope.kind.value, scope.key)

    async def load(self, scope: ConfigScope) -> Optional[dict]:
        override = self._store.get(self._key(scope))
        return copy.deepcopy(override) if override is not None else None

    async def update(self, scope: ConfigScope, mutate: Mutate) -> dict:
        async with self._lock:
            key = self._key(scope)
            current = self._store.get(key)
            new_override = mutate(copy.deepcopy(current) if current else None)
            self._store[key] = copy.deepcopy(new_override)
            return copy.deepcopy(new_override)

    async def delete(self, scope: ConfigScope) -> None:
        async with self._lock:
            self._store.pop(self._key(scope), None)


@register_config_source("memory")
def _build_memory_source(ctx: ConfigSourceContext) -> MemoryConfigSource:
    return MemoryConfigSource(ctx)
