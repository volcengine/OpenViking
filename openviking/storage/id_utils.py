# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic record ID utilities for VectorDB.

Provides a single source of truth for computing record IDs from URIs and levels.
Used by collection_schemas, viking_vector_index_backend, and resource_service.
"""

import hashlib
from typing import Any


def seed_uri_for_id(uri: str, level: Any) -> str:
    """Build deterministic id seed URI from canonical uri + hierarchy level.

    Args:
        uri: Viking URI (e.g., "viking://resources/doc_name")
        level: Context level (0=abstract, 1=overview, 2=detail)

    Returns:
        Seed URI with appropriate suffix for the given level.
    """
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 2

    if level_int == 0:
        return uri if uri.endswith("/.abstract.md") else f"{uri}/.abstract.md"
    if level_int == 1:
        return uri if uri.endswith("/.overview.md") else f"{uri}/.overview.md"
    return uri


def compute_record_id(account_id: str, uri: str, level: Any) -> str:
    """Compute deterministic VectorDB record ID for a given URI and level.

    Args:
        account_id: Tenant account ID
        uri: Viking URI
        level: Context level (0=abstract, 1=overview, 2=detail)

    Returns:
        MD5 hex digest used as the VectorDB record ID.
    """
    seed = seed_uri_for_id(uri, level)
    id_seed = f"{account_id}:{seed}"
    return hashlib.md5(id_seed.encode("utf-8")).hexdigest()
