# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for memory chunk URI suffixes.

Real chunk URIs are ``{base_uri}#chunk_{idx:04d}`` (see
``ReindexExecutor._chunk_memory_body``): the chunk marker is always the
final segment of the URI. A '#' elsewhere is a literal path character
(accepted by add-resource and write), never a fragment.

Production (``make_chunk_uri``), recognition (``chunk_base_uri``), and
query pre-filtering (``CHUNK_MARKER``) all derive from this module so the
format has a single source of truth.
"""

import re
from typing import Optional

CHUNK_MARKER = "#chunk_"

_CHUNK_SUFFIX_RE = re.compile(rf"{re.escape(CHUNK_MARKER)}\d+$")


def make_chunk_uri(base_uri: str, index: int) -> str:
    """Return the chunk URI for *index* of *base_uri*."""
    return f"{base_uri}{CHUNK_MARKER}{index:04d}"


def chunk_base_uri(uri: str) -> Optional[str]:
    """Return the base URI when *uri* ends with a chunk suffix, else None."""
    match = _CHUNK_SUFFIX_RE.search(uri)
    return uri[: match.start()] if match else None


def strip_chunk_suffix(uri: str) -> str:
    """Return *uri* with a trailing chunk suffix removed, if present."""
    base = chunk_base_uri(uri)
    return uri if base is None else base
