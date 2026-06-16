# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session metadata helpers: validation, merging, prompt rendering.

Sessions can carry a free-form ``metadata: dict[str, Any]`` field used to
express project-level personalization (architectural style, tech-stack
preferences, project name, etc.). Limits are intentionally hard-coded as
module constants — there is no config field for them.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

# Maximum serialized JSON size for ``Session.metadata`` in bytes.
METADATA_MAX_BYTES: int = 16 * 1024
# Maximum number of top-level keys.
METADATA_MAX_KEYS: int = 64


class MetadataValidationError(ValueError):
    """Raised when session metadata fails validation."""


def validate_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Validate a metadata dict against size, key-count and JSON-serializability.

    Returns the original dict (unchanged) when valid. Raises
    :class:`MetadataValidationError` otherwise. ``None`` is allowed and passes
    through.
    """
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise MetadataValidationError("metadata must be a JSON object")
    if len(metadata) > METADATA_MAX_KEYS:
        raise MetadataValidationError(
            f"metadata has {len(metadata)} keys (max {METADATA_MAX_KEYS})"
        )
    try:
        encoded = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MetadataValidationError(f"metadata is not JSON-serializable: {exc}") from exc
    if len(encoded) > METADATA_MAX_BYTES:
        raise MetadataValidationError(
            f"metadata is {len(encoded)} bytes (max {METADATA_MAX_BYTES})"
        )
    return metadata


def merge_metadata(
    existing: Optional[Dict[str, Any]],
    incoming: Dict[str, Any],
    *,
    replace: bool = False,
) -> Dict[str, Any]:
    """Merge ``incoming`` into ``existing`` (or replace entirely).

    With ``replace=False`` (default) keys from ``incoming`` overwrite matching
    keys in ``existing`` while preserving the rest. With ``replace=True`` the
    result is exactly ``incoming``.
    """
    if replace:
        return dict(incoming)
    merged: Dict[str, Any] = dict(existing or {})
    merged.update(incoming)
    return merged


def render_metadata_prompt_block(metadata: Optional[Dict[str, Any]]) -> str:
    """Render a delimited ``[Session metadata]`` block for the LLM system prompt.

    Returns an empty string when ``metadata`` is ``None`` or empty so callers
    can unconditionally concatenate the result.
    """
    if not metadata:
        return ""
    lines = ["[Session metadata]"]
    for key, value in metadata.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("[/Session metadata]")
    return "\n".join(lines)
