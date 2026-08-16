# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stable digests for vector-index source data and repair plans."""

from __future__ import annotations

import hashlib
import json
from typing import Any

DIGEST_PREFIX = "sha256:v1:"


def normalize_source_text(text: str) -> str:
    """Normalize only line endings in text used for vectorization."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def source_digest(text: str) -> str:
    """Return the versioned digest of final text sent for vectorization."""
    payload = normalize_source_text(text).encode("utf-8")
    return f"{DIGEST_PREFIX}{hashlib.sha256(payload).hexdigest()}"


def canonical_json(value: Any) -> str:
    """Serialize JSON data deterministically without insignificant whitespace."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    """Return a versioned digest for canonical JSON-compatible data."""
    return source_digest(canonical_json(value))


def embedding_input_digest(value: str | list[dict[str, Any]]) -> str:
    """Digest the exact string or multimodal payload submitted to an embedder."""
    if isinstance(value, str):
        return source_digest(value)
    return canonical_digest(value)
