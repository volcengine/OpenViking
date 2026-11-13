# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only content cache for the VikingFS read path (in-memory LRU + TTL).

Design notes
  1. Cache only the text produced by ``read_file``. A hit still runs the
     authorization check but skips the AGFS read.
  2. Keys are ``(account, user, uri)``, so entries are isolated per user and
     never leak across accounts.
  3. Writes, appends, deletes, and moves invalidate entries eagerly; the TTL
     (600 seconds by default) is only a safety net.
  4. ``stats()`` exposes hit/miss counters so benchmark reports can state the
     caching policy they measured.

Disable with ``OPENVIKING_CONTENT_CACHE=0`` (``false``/``off``/``no``/
``disable``/``disabled`` also work). Override the TTL with
``OPENVIKING_CONTENT_CACHE_TTL`` (read at import time; restart to apply).
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

_ENV = "OPENVIKING_CONTENT_CACHE"
_OFF_VALUES = {"0", "false", "off", "no", "disable", "disabled"}
_TTL_SECONDS = float(os.getenv("OPENVIKING_CONTENT_CACHE_TTL", "600.0"))
_MAX_ENTRIES = int(os.getenv("OPENVIKING_CONTENT_CACHE_MAX_ENTRIES", "4096"))
_MAX_ENTRY_BYTES = int(os.getenv("OPENVIKING_CONTENT_CACHE_MAX_ENTRY_BYTES", str(256 * 1024)))

CacheKey = Tuple[str, str, str]


def cache_enabled() -> bool:
    """Return whether caching is enabled (enabled unless explicitly turned off)."""
    return os.getenv(_ENV, "").strip().lower() not in _OFF_VALUES


def make_key(ctx: Any, uri: str) -> CacheKey:
    """Build an ``(account, user, uri)`` key so users never share entries."""
    user = getattr(ctx, "user", None)
    account = str(getattr(user, "account_id", None) or getattr(ctx, "account_id", "") or "")
    uid = str(getattr(user, "user_id", None) or getattr(user, "uid", None) or user or "")
    return (account, uid, uri)


class ReadContentCache:
    """LRU text cache with a TTL; hits and invalidations are counted."""

    def __init__(self) -> None:
        self._data: "OrderedDict[CacheKey, Tuple[str, float]]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.invalidations = 0
        self.stores = 0

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: CacheKey) -> Optional[str]:
        if not cache_enabled():
            return None
        item = self._data.get(key)
        if item is None:
            self.misses += 1
            return None
        text, expire_at = item
        if expire_at <= time.monotonic():
            self._data.pop(key, None)
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return text

    def put(self, key: CacheKey, text: str) -> None:
        if not cache_enabled() or not text or len(text) > _MAX_ENTRY_BYTES:
            return
        self._data[key] = (text, time.monotonic() + _TTL_SECONDS)
        self._data.move_to_end(key)
        self.stores += 1
        while len(self._data) > _MAX_ENTRIES:
            self._data.popitem(last=False)

    def invalidate(self, uri: str) -> int:
        """Invalidate one exact URI for every user."""
        keys = [k for k in self._data if k[2] == uri]
        for k in keys:
            self._data.pop(k, None)
        self.invalidations += len(keys)
        return len(keys)

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate a directory prefix (used when deleting/moving a subtree)."""
        keys = [k for k in self._data if k[2].startswith(prefix)]
        for k in keys:
            self._data.pop(k, None)
        self.invalidations += len(keys)
        return len(keys)

    def clear(self) -> None:
        self._data.clear()

    def reset_stats(self) -> None:
        self.hits = self.misses = self.invalidations = self.stores = 0

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "enabled": cache_enabled(),
            "entries": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "invalidations": self.invalidations,
            "hit_rate": (self.hits / total) if total else 0.0,
            "ttl_s": _TTL_SECONDS,
            "max_entries": _MAX_ENTRIES,
        }


CACHE = ReadContentCache()
