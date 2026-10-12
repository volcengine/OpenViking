# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded process-local cache with one loader per key and short revocation lag."""

import asyncio
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, ttl, capacity):
        self.ttl, self.capacity = ttl, capacity
        self.entries = OrderedDict()

    async def get(self, key, load):
        now = time.monotonic()
        entry = self.entries.get(key)
        if entry is None or entry[0] <= now:
            task = asyncio.create_task(load())
            entry = (now + self.ttl, task)
            self.entries[key] = entry
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
        self.entries.move_to_end(key)
        try:
            return await asyncio.shield(entry[1])
        except Exception:
            if self.entries.get(key) is entry:
                self.entries.pop(key, None)
            raise

    def clear(self):
        self.entries.clear()
