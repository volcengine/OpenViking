# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""JSON helpers that keep vectordb fields UTF-8 safe."""

from __future__ import annotations

import json
from typing import Any


def sanitize_unicode_for_json(value: Any) -> Any:
    """Replace isolated UTF-16 surrogate code points before JSON storage.

    Python's JSON decoder accepts lone surrogates such as ``"\\ud800"`` and
    represents them as surrogate code points in ``str``. Those strings cannot be
    encoded as valid UTF-8 and can break lower-level index recovery. The UTF-16
    round trip keeps valid non-BMP characters intact while replacing isolated
    surrogates with U+FFFD.
    """
    if isinstance(value, str):
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {
            sanitize_unicode_for_json(key): sanitize_unicode_for_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_unicode_for_json(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_unicode_for_json(item) for item in value)
    return value


def safe_json_dumps(value: Any, **kwargs: Any) -> str:
    """Dump JSON after removing UTF-8-invalid surrogate code points."""
    return json.dumps(sanitize_unicode_for_json(value), **kwargs)
