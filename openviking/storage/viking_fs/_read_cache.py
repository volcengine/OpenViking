# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only content cache for the VikingFS read path (in-memory LRU + versions + TTL).

Design notes:
  1. Only text returned by read_file is cached. URI resolution and the
     authorization check (_ensure_access) still run on every read; the cache is
     consulted only after authorization passes, so a hit skips path mapping and
     the AGFS read.
  2. The key is (account, user, uri), which isolates users from each other.
  3. Writes, appends, deletes and moves invalidate by bumping the URI version
     instead of only dropping entries. Each entry stores the version observed
     before the underlying read, and a mismatch is treated as a miss. This
     prevents a stale value from surviving a "read miss -> concurrent write ->
     late refill" race.
  4. Consistency is guaranteed within a single process only. With multiple
     workers (workers > 1) the cache disables itself at startup because worker
     processes cannot see each other's invalidations. For cross-process caching
     use the upstream CacheFS (storage.agfs.cachefs).

Disable with OPENVIKING_CONTENT_CACHE=0 (false/off/no/disable also work).
TTL can be overridden with OPENVIKING_CONTENT_CACHE_TTL (read at import time).
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple


_ENV = "OPENVIKING_CONTENT_CACHE"
_OFF_VALUES = {"0", "false", "off", "no", "disable", "disabled"}
_TTL_SECONDS = float(os.getenv("OPENVIKING_CONTENT_CACHE_TTL", "600.0"))
_MAX_ENTRIES = int(os.getenv("OPENVIKING_CONTENT_CACHE_MAX_ENTRIES", "4096"))
_MAX_ENTRY_BYTES = int(
    os.getenv("OPENVIKING_CONTENT_CACHE_MAX_ENTRY_BYTES", str(256 * 1024))
)
_MAX_VERSION_ENTRIES = 65536

CacheKey = Tuple[str, str, str]
CacheVersion = Tuple[int, int]  # (per-uri version, prefix epoch)


def cache_enabled() -> bool:
    """Enabled by default; only an explicit off value takes the native path."""
    return os.getenv(_ENV, "").strip().lower() not in _OFF_VALUES


def disable_for_multi_worker(workers: int) -> bool:
    """Disable the content cache when more than one worker is configured.

    Each worker is a separate process with its own in-memory cache and cannot
    observe writes performed by its peers, so we fall back to the uncached path.
    A single process (workers <= 1) is untouched, and an explicit
    OPENVIKING_CONTENT_CACHE setting always wins.

    Returns True when the cache was disabled by this call.
    """
    if workers <= 1:
        return False
    if os.getenv(_ENV) is not None:
        return False
    os.environ[_ENV] = "0"
    return True


def make_key(ctx: Any, uri: str) -> CacheKey:
    """Build a key from (account, user, uri) so users never share entries."""
    user = getattr(ctx, "user", None)
    account = str(
        getattr(user, "account_id", None) or getattr(ctx, "account_id", "") or ""
    )
    uid = str(
        getattr(user, "user_id", None) or getattr(user, "uid", None) or user or ""
    )
    return (account, uid, uri)


class ReadContentCache:
    """TTL-bounded LRU text cache; entries carry a version, invalidation bumps it."""

    def __init__(self) -> None:
        self._data: "OrderedDict[CacheKey, Tuple[str, float, CacheVersion]]" = (
            OrderedDict()
        )
        self._by_uri: Dict[str, set] = {}
        self._ver: Dict[str, int] = {}
        self._prefix_epoch = 0
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.invalidations = 0
        self.stores = 0
        self.version_misses = 0
        self.bumps = 0

    def __len__(self) -> int:
        return len(self._data)

    # ---------- versions ----------

    def version(self, uri: str) -> CacheVersion:
        """Read the version before the underlying read; writers bump afterwards."""
        with self._lock:
            return (self._ver.get(uri, 0), self._prefix_epoch)

    def bump(self, uri: str) -> None:
        """Called after a successful write; invalidates cached and in-flight entries."""
        with self._lock:
            self._ver[uri] = self._ver.get(uri, 0) + 1
            self.bumps += 1
            if len(self._ver) > _MAX_VERSION_ENTRIES:
                # Bound memory usage: clearing the table and moving the prefix
                # epoch forward makes every older entry mismatch.
                self._ver.clear()
                self._prefix_epoch += 1

    # ---------- read / write ----------

    def get(
        self, key: CacheKey, version: Optional[CacheVersion] = None
    ) -> Optional[str]:
        if not cache_enabled():
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            text, expire_at, ver = item
            if version is not None and ver != version:
                self._drop(key)
                self.misses += 1
                self.version_misses += 1
                return None
            if expire_at <= time.monotonic():
                self._drop(key)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return text

    def put(
        self,
        key: CacheKey,
        text: str,
        version: Optional[CacheVersion] = None,
    ) -> None:
        if not cache_enabled() or not text or len(text) > _MAX_ENTRY_BYTES:
            return
        with self._lock:
            self._data[key] = (
                text,
                time.monotonic() + _TTL_SECONDS,
                version if version is not None else (0, 0),
            )
            self._by_uri.setdefault(key[2], set()).add(key)
            self._data.move_to_end(key)
            self.stores += 1
            while len(self._data) > _MAX_ENTRIES:
                oldest, _ = self._data.popitem(last=False)
                self._discard_index(oldest)

    def _discard_index(self, key: CacheKey) -> None:
        keys = self._by_uri.get(key[2])
        if keys is not None:
            keys.discard(key)
            if not keys:
                self._by_uri.pop(key[2], None)

    def _drop(self, key: CacheKey) -> None:
        self._data.pop(key, None)
        self._discard_index(key)

    # ---------- invalidation ----------

    def invalidate(self, uri: str) -> int:
        """Invalidate one URI for every user: bump first, then drop entries."""
        with self._lock:
            self.bump(uri)
            keys = self._by_uri.pop(uri, set())
            for k in keys:
                self._data.pop(k, None)
            self.invalidations += len(keys)
            return len(keys)

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate a directory subtree (used by rm/mv): advance the prefix epoch."""
        with self._lock:
            self._prefix_epoch += 1
            keys = [k for k in self._data if k[2].startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
                self._discard_index(k)
            self.invalidations += len(keys)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._by_uri.clear()

    def reset_stats(self) -> None:
        with self._lock:
            self.hits = self.misses = self.invalidations = self.stores = 0
            self.version_misses = self.bumps = 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "enabled": cache_enabled(),
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
                "invalidations": self.invalidations,
                "version_misses": self.version_misses,
                "bumps": self.bumps,
                "hit_rate": (self.hits / total) if total else 0.0,
                "ttl_s": _TTL_SECONDS,
                "max_entries": _MAX_ENTRIES,
            }


CACHE = ReadContentCache()
