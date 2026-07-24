# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stable source metadata persisted with an ingested resource tree."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Mapping, Optional

from openviking.storage.internal_names import RESOURCE_SOURCE_METADATA_FILE

SOURCE_METADATA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_source_fingerprint(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate the caller-independent fingerprint fields used by write policies."""
    if value is None:
        return {"source_kind": "unfingerprinted"}
    if not isinstance(value, Mapping):
        raise ValueError("source_fingerprint must be an object")

    source_kind = value.get("source_kind")
    source_sha256 = value.get("source_sha256")
    source_size = value.get("source_size")
    if source_kind != "temp_upload":
        raise ValueError("source_fingerprint.source_kind must be 'temp_upload'")
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise ValueError("source_fingerprint.source_sha256 must be a lowercase SHA-256")
    if not isinstance(source_size, int) or isinstance(source_size, bool) or source_size < 0:
        raise ValueError("source_fingerprint.source_size must be a non-negative integer")
    return {
        "source_kind": source_kind,
        "source_sha256": source_sha256,
        "source_size": source_size,
        "source_revision": f"sha256:{source_sha256}",
    }


def build_source_metadata(fingerprint: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Create metadata for a newly accepted resource write."""
    return {
        "version": SOURCE_METADATA_VERSION,
        **normalize_source_fingerprint(fingerprint),
        "target_revision": uuid.uuid4().hex,
    }


def encode_source_metadata(metadata: Mapping[str, Any]) -> str:
    return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_source_metadata(raw: str | bytes) -> Optional[dict[str, Any]]:
    """Return validated persisted metadata, or ``None`` for legacy/invalid data."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("version") != SOURCE_METADATA_VERSION:
        return None
    target_revision = value.get("target_revision")
    if not isinstance(target_revision, str) or not target_revision:
        return None
    try:
        normalized = normalize_source_fingerprint(value)
    except ValueError:
        if value.get("source_kind") != "unfingerprinted":
            return None
        normalized = {"source_kind": "unfingerprinted"}
    return {
        "version": SOURCE_METADATA_VERSION,
        **normalized,
        "target_revision": target_revision,
    }


def source_metadata_uri(resource_uri: str) -> str:
    return f"{resource_uri.rstrip('/')}/{RESOURCE_SOURCE_METADATA_FILE}"


def fingerprints_match(
    persisted: Optional[Mapping[str, Any]], incoming: Mapping[str, Any]
) -> bool:
    """Compare only same-kind stable source identities."""
    if not persisted:
        return False
    return (
        persisted.get("source_kind") == incoming.get("source_kind") == "temp_upload"
        and persisted.get("source_sha256") == incoming.get("source_sha256")
    )
