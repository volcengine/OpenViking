# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-wide LRU result cache for rerank provider calls.

Identical inputs deterministically produce identical provider scores, so
caching is correctness-preserving. The key is a sha256 digest over
length-prefixed scoring-identity fields plus the exact query and document
texts in request order; only the digest is retained as key material and it
is never logged. Failures are never cached. Not tenant-scoped by design:
rerank configuration is process-global and startup-only (changing it
requires a restart, which also clears this cache), so identical inputs
score identically for every account. Cache hits skip the provider call and
therefore emit no rerank token-usage or call metrics.
"""

import hashlib
import struct
import threading
import time
from collections import OrderedDict
from typing import Any, List, Optional, Tuple

_CACHE_MAX_ENTRIES = 1024
_CACHE_TTL_SECONDS = 24 * 3600.0
# Request limits are unbounded upstream, so an entry count alone is not a
# memory bound; payloads above this size skip caching.
_CACHE_MAX_KEY_BYTES = 1_000_000

_lock = threading.Lock()
_cache: "OrderedDict[str, Tuple[float, List[float]]]" = OrderedDict()
_IDENTITY_ATTRS = ("model_name", "model", "model_version", "host", "api_base", "mode")


def cache_key(client: Any, query: str, documents: List[str]) -> Optional[str]:
    """Return the entry key, or None when the payload exceeds the byte cap."""
    fields: List[Tuple[str, str]] = [("provider", str(getattr(client, "provider", "")))]
    for name in _IDENTITY_ATTRS:
        value = getattr(client, name, None)
        if value is not None:
            fields.append((name, str(value)))
    fields.append(("query", query))
    fields.extend((f"doc{i}", doc) for i, doc in enumerate(documents))
    digest = hashlib.sha256()
    budget = _CACHE_MAX_KEY_BYTES
    for _, value in fields:
        chunk = value.encode("utf-8", "replace")
        budget -= 8 + len(chunk)
        if budget < 0:
            return None
        digest.update(struct.pack("<Q", len(chunk)))
        digest.update(chunk)
    return digest.hexdigest()


def cache_get(key: str) -> Optional[List[float]]:
    """Return a fresh cached score list; refreshes LRU recency on a hit."""
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stored_at, scores = entry
        if time.monotonic() - stored_at > _CACHE_TTL_SECONDS:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return list(scores)


def cache_put(key: str, scores: List[float]) -> None:
    """Store a successful score list; evicts least-recently-used past capacity."""
    with _lock:
        _cache[key] = (time.monotonic(), list(scores))
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


class CachedRerankClient:
    """Transparent cache wrapper around a rerank client instance.

    Only ``rerank_batch`` is intercepted; every other attribute (provider,
    model identity, ``from_config`` results) delegates to the wrapped client.
    Successful, non-None responses are cached; exceptions and ``None``
    propagate exactly as before.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        key = cache_key(self._client, query, documents)
        if key is not None:
            cached = cache_get(key)
            if cached is not None:
                return cached
        scores = self._client.rerank_batch(query, documents)
        if key is not None and scores is not None:
            cache_put(key, scores)
        return scores

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
