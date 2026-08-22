# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Pure sparse-term mapping primitives used by the Qdrant backend."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from threading import RLock
from typing import Any

SparseResolveTerm = Callable[[str], int | None]
SparseResolveIndex = Callable[[int], str | None]
SparsePersist = Callable[[str, int], Any]
_MAX_QDRANT_SPARSE_INDEX = 0x7FFF_FFFF
_SPARSE_TERM_LOCK = RLock()


def stable_sparse_index(term: str) -> int:
    """Return a positive Qdrant-compatible sparse index derived from a term."""
    digest = hashlib.sha256(term.encode("utf-8")).digest()
    # Qdrant's sparse indices are uint32 on the REST/protobuf boundary.
    value = int.from_bytes(digest[:4], "big") & _MAX_QDRANT_SPARSE_INDEX
    return value or 1


class SparseTermDictionary:
    """Resolve string sparse terms to durable numeric Qdrant indices."""

    def __init__(
        self,
        *,
        resolve_term: SparseResolveTerm,
        resolve_index: SparseResolveIndex,
        persist: SparsePersist,
        hash_term: Callable[[str], int] = stable_sparse_index,
    ) -> None:
        self._resolve_term = resolve_term
        self._resolve_index = resolve_index
        self._persist = persist
        self._hash_term = hash_term

    def index_for(self, term: str) -> int:
        with _SPARSE_TERM_LOCK:
            normalized = str(term)
            existing = self._resolve_term(normalized)
            if existing is not None:
                return int(existing)

            candidate = int(self._hash_term(normalized))
            if not 0 < candidate <= _MAX_QDRANT_SPARSE_INDEX:
                raise ValueError(
                    "sparse term index must be a positive Qdrant-compatible uint32"
                )
            owner = self._resolve_index(candidate)
            if owner is not None and owner != normalized:
                raise ValueError(
                    "sparse term index collision: "
                    f"index={candidate} existing_term={owner!r} new_term={normalized!r}"
                )
            self._persist(normalized, candidate)
            owner = self._resolve_index(candidate)
            if owner is not None and owner != normalized:
                raise ValueError(
                    "sparse term index collision after persistence: "
                    f"index={candidate} existing_term={owner!r} new_term={normalized!r}"
                )
            return candidate

    def encode(self, vector: dict[str, float] | None) -> dict[str, list[Any]] | None:
        if not vector:
            return None
        indices: list[int] = []
        values: list[float] = []
        for term, weight in vector.items():
            value = float(weight)
            if not math.isfinite(value):
                raise ValueError("sparse vector weights must be finite")
            indices.append(self.index_for(str(term)))
            values.append(value)
        return {"indices": indices, "values": values}
