# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""In-memory store for short-lived upload tokens used by the MCP progressive upload flow.

Issued by the MCP ``add_resource`` tool when a caller passes a local-file path; consumed by
``POST /api/v1/resources/temp_upload_signed``. Tokens are 6-character base62 strings indexed
in a process-local dict — no signing key, no on-disk state, no replay-set bookkeeping.
``dict.pop`` doubles as the consume-and-burn primitive.

The token only carries the identity bound at issue time (account/user). The
``temp_file_id`` is minted by :class:`openviking.server.temp_upload_store.TempUploadStore`
at upload time and returned in the upload response body — the token does
not pre-bind a filename, so the upload can flow through either the local or shared
TempUploadStore mode without a side-channel.

Single-worker only: tokens are lost on restart and not shared across uvicorn workers.
Multi-worker deployments should rely on the shared-mode TempUploadStore for the actual
file payload; only the brief upload-token handshake is process-local.
"""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from typing import Optional, Tuple

_TOKEN_ALPHABET = string.ascii_letters + string.digits  # base62
_TOKEN_LENGTH = 6


class UploadTokenError(Exception):
    """Raised when an upload token is unknown or expired."""


@dataclass(frozen=True)
class _TokenInfo:
    account_id: str
    user_id: str
    expires_at: float


class UploadTokenStore:
    def __init__(self) -> None:
        self._store: dict[str, _TokenInfo] = {}

    def issue(
        self,
        account_id: str,
        user_id: str,
        ttl_seconds: int,
    ) -> Tuple[str, float]:
        """Mint a fresh token bound to (account, user). Returns (token, expires_at)."""
        self._purge_expired()
        expires_at = time.time() + max(1, ttl_seconds)
        info = _TokenInfo(account_id, user_id, expires_at)
        for _ in range(8):
            token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))
            if token not in self._store:
                self._store[token] = info
                return token, expires_at
        raise RuntimeError("upload_token_store: failed to mint a unique token after 8 attempts")

    def consume(self, token: str) -> Tuple[str, str]:
        """Pop the token and validate it. Returns (account_id, user_id) on success."""
        if not token:
            raise UploadTokenError("missing upload token")
        info = self._store.pop(token, None)
        if info is None:
            raise UploadTokenError("unknown or already-consumed upload token")
        if info.expires_at < time.time():
            raise UploadTokenError("upload token expired")
        return info.account_id, info.user_id

    def peek(self, token: str) -> Optional[_TokenInfo]:
        """Read a token without consuming. Test helper only."""
        return self._store.get(token)

    def clear(self) -> None:
        """Reset state. Test helper only."""
        self._store.clear()

    def _purge_expired(self) -> None:
        now = time.time()
        for token, info in list(self._store.items()):
            if info.expires_at < now:
                self._store.pop(token, None)


upload_token_store = UploadTokenStore()
