# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local BM25 sparse embedder for hybrid retrieval without external dependencies."""

from __future__ import annotations

import json
import logging
import math
import re
import zlib
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from openviking.models.embedder.base import EmbedResult, SparseEmbedderBase

logger = logging.getLogger(__name__)

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75
DEFAULT_TOKEN_PATTERN = r"\w+"


class BM25Stats:
    """Thread-safe corpus statistics for BM25 scoring."""

    def __init__(self) -> None:
        self.doc_count: int = 0
        self.total_tokens: int = 0
        self.term_doc_freq: Dict[int, int] = {}
        self._lock = Lock()

    @property
    def avgdl(self) -> float:
        if self.doc_count == 0:
            return 1.0
        return self.total_tokens / self.doc_count

    def add_document(self, token_hashes: List[int], doc_len: int) -> None:
        with self._lock:
            self.doc_count += 1
            self.total_tokens += doc_len
            seen = set(token_hashes)
            for h in seen:
                self.term_doc_freq[h] = self.term_doc_freq.get(h, 0) + 1

    def save(self, path: Path) -> None:
        with self._lock:
            data = {
                "version": 1,
                "doc_count": self.doc_count,
                "total_tokens": self.total_tokens,
                "term_doc_freq": {str(k): v for k, v in self.term_doc_freq.items()},
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self.doc_count = raw.get("doc_count", 0)
                self.total_tokens = raw.get("total_tokens", 0)
                self.term_doc_freq = {int(k): v for k, v in raw.get("term_doc_freq", {}).items()}
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning("bm25: failed to load stats from %s: %s", path, e)


def _tokenize(text: str, pattern: str = DEFAULT_TOKEN_PATTERN) -> List[str]:
    """Tokenize text: lowercase + regex word extraction."""
    return re.findall(pattern, text.lower())


def _hash_token(token: str) -> int:
    """CRC32 hash of token, matching Milvus approach."""
    return zlib.crc32(token.encode("utf-8")[:128]) & 0xFFFFFFFF


class LocalBM25Embedder(SparseEmbedderBase):
    """BM25 sparse embedder for local hybrid retrieval.

    Insert path (is_query=False): returns length-normalized TF vector.
    Query path (is_query=True): returns IDF-weighted query vector.
    Dot product of query x document = BM25 score.
    """

    def __init__(
        self,
        model_name: str = "bm25",
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        token_pattern: str = DEFAULT_TOKEN_PATTERN,
        stats_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name=model_name, config=config)
        self.k1 = k1
        self.b = b
        self.token_pattern = token_pattern
        self.stats = BM25Stats()
        self._stats_path: Optional[Path] = Path(stats_path) if stats_path else None
        if self._stats_path:
            self.stats.load(self._stats_path)

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        tokens = _tokenize(text, self.token_pattern)
        if not tokens:
            return EmbedResult(sparse_vector={})

        token_hashes = [_hash_token(t) for t in tokens]

        if is_query:
            return self._embed_query(token_hashes)
        return self._embed_document(token_hashes)

    def _embed_document(self, token_hashes: List[int]) -> EmbedResult:
        doc_len = len(token_hashes)
        avgdl = self.stats.avgdl

        tf_counts: Dict[int, int] = {}
        for h in token_hashes:
            tf_counts[h] = tf_counts.get(h, 0) + 1

        sparse: Dict[str, float] = {}
        for h, tf in tf_counts.items():
            norm_tf = tf / (tf + self.k1 * (1 - self.b + self.b * doc_len / avgdl))
            sparse[str(h)] = norm_tf

        self.stats.add_document(token_hashes, doc_len)
        if self._stats_path:
            self.stats.save(self._stats_path)

        return EmbedResult(sparse_vector=sparse)

    def _embed_query(self, token_hashes: List[int]) -> EmbedResult:
        doc_count = self.stats.doc_count
        if doc_count == 0:
            return EmbedResult(sparse_vector={})

        seen: Dict[int, int] = {}
        for h in token_hashes:
            seen[h] = seen.get(h, 0) + 1

        sparse: Dict[str, float] = {}
        for h in seen:
            df = self.stats.term_doc_freq.get(h, 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            sparse[str(h)] = idf * (self.k1 + 1)

        return EmbedResult(sparse_vector=sparse)

    def close(self) -> None:
        if self._stats_path:
            self.stats.save(self._stats_path)
