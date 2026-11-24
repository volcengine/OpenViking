"""Stable hashes for task inputs and conditional file writes."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def digest(value: Any) -> str:
    """Hash JSON-compatible task inputs deterministically, including their versions."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def content_hash(value: str | bytes) -> str:
    """Return the byte hash used by conditional content writes."""
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()
