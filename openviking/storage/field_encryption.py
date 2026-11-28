# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Client-side encryption for credentials stored opaquely by TaskService."""

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "ovenc1:"
OBJECT_KEY = "$ovenc1"
NONCE_SIZE = 12


class FieldCipher:
    def __init__(self, key: bytes):
        self._aead = AESGCM(key)

    def transform(self, payload: Any, paths: tuple[str, ...], *, encrypt: bool) -> Any:
        """Transform a freshly serialized JSON payload; null and absent fields stay unchanged."""
        for path in paths:
            segments = path.split(".")
            # A stored value may be returned by any read route or copied in a
            # response to the write that produced it.
            aad = f"ov-store-credential-v1\n{segments[-1]}".encode()
            payload = self._visit(payload, segments, aad, encrypt)
        return payload

    def _visit(self, value: Any, path: list[str], aad: bytes, encrypt: bool) -> Any:
        if value is None:
            return None
        if not path:
            if encrypt:
                nonce = os.urandom(NONCE_SIZE)
                raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
                sealed = (
                    PREFIX + base64.b64encode(nonce + self._aead.encrypt(nonce, raw, aad)).decode()
                )
                return {OBJECT_KEY: sealed} if isinstance(value, dict) else sealed
            if isinstance(value, dict) and set(value) == {OBJECT_KEY}:
                value = value[OBJECT_KEY]
            if not isinstance(value, str) or not value.startswith(PREFIX):
                raise ValueError("Expected encrypted store field")
            try:
                raw = base64.b64decode(value[len(PREFIX) :], validate=True)
                return json.loads(self._aead.decrypt(raw[:NONCE_SIZE], raw[NONCE_SIZE:], aad))
            except (ValueError, InvalidTag):
                raise ValueError("Cannot decrypt store field") from None
        part, *rest = path
        if isinstance(value, dict) and part in value:
            value[part] = self._visit(value[part], rest, aad, encrypt)
        elif isinstance(value, list):
            if part == "*":
                for i, item in enumerate(value):
                    value[i] = self._visit(item, rest, aad, encrypt)
            elif part.isdecimal() and int(part) < len(value):
                i = int(part)
                value[i] = self._visit(value[i], rest, aad, encrypt)
        return value
