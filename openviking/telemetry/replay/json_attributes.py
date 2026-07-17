# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import base64
import json
import zlib
from typing import Any

_COMPRESSED_PREFIX = "zlib+base64:"
_COMPRESSION_THRESHOLD_BYTES = 16 * 1024


def encode_json_attribute(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    raw = text.encode("utf-8")
    if len(raw) < _COMPRESSION_THRESHOLD_BYTES:
        return text
    compressed = base64.b64encode(zlib.compress(raw)).decode("ascii")
    return f"{_COMPRESSED_PREFIX}{compressed}"


def decode_json_attribute(value: str) -> Any:
    text = value
    if value.startswith(_COMPRESSED_PREFIX):
        encoded = value.removeprefix(_COMPRESSED_PREFIX)
        try:
            compressed = base64.b64decode(encoded, validate=True)
            text = zlib.decompress(compressed).decode("utf-8")
        except (ValueError, zlib.error, UnicodeDecodeError) as error:
            raise ValueError("invalid compressed replay JSON") from error
    return json.loads(text)
