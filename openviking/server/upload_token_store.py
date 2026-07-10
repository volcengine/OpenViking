# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""In-memory store for short-lived upload tokens used by the MCP progressive upload flow.

Issued by the MCP ``add_resource`` tool when a caller passes a local-file path; consumed by
``POST /api/v1/resources/temp_upload`` when the request carries a ``?token=`` instead of an
API key. Tokens are 6-character base62 strings indexed in a process-local dict — no signing
key, no on-disk state, no replay-set bookkeeping. ``dict.pop`` doubles as the
consume-and-burn primitive.

The token carries the identity bound at issue time (account/user), the caller's actor peer
scope (``actor_peer_id``), and the business params (``to``/``reason``) so the server can
finish ingestion automatically once the file lands — the caller does not re-invoke
``add_resource``, and the ingest keeps the original peer scope. The ``temp_file_id`` is minted by
:class:`openviking.server.temp_upload_store.TempUploadStore` at upload time, so the token
does not pre-bind a filename and the upload can flow through either the local or shared
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
    to: str
    reason: str
    actor_peer_id: str
    expires_at: float


@dataclass(frozen=True)
class ConsumedUploadToken:
    """Identity and business params bound to an upload token, returned by ``consume``."""

    account_id: str
    user_id: str
    to: str
    reason: str
    actor_peer_id: str


class UploadTokenStore:
    def __init__(self) -> None:
        self._store: dict[str, _TokenInfo] = {}

    def issue(
        self,
        account_id: str,
        user_id: str,
        ttl_seconds: int,
        *,
        to: str = "",
        reason: str = "",
        actor_peer_id: str = "",
    ) -> Tuple[str, float]:
        """Mint a fresh token bound to (account, user) plus ``to``/``reason``/``actor_peer_id``.

        ``actor_peer_id`` is captured from the minting request's context so server-side
        auto-ingest keeps the caller's peer scope (reason-memory routing) — it is NOT taken
        from the later upload request's headers, which could be spoofed. Returns
        (token, expires_at).
        """
        self._purge_expired()
        expires_at = time.time() + max(1, ttl_seconds)
        info = _TokenInfo(account_id, user_id, to, reason, actor_peer_id, expires_at)
        for _ in range(8):
            token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))
            if token not in self._store:
                self._store[token] = info
                return token, expires_at
        raise RuntimeError("upload_token_store: failed to mint a unique token after 8 attempts")

    def consume(self, token: str) -> ConsumedUploadToken:
        """Pop the token and validate it. Returns the bound identity + business params."""
        if not token:
            raise UploadTokenError("missing upload token")
        info = self._store.pop(token, None)
        if info is None:
            raise UploadTokenError("unknown or already-consumed upload token")
        if info.expires_at < time.time():
            raise UploadTokenError("upload token expired")
        return ConsumedUploadToken(
            info.account_id, info.user_id, info.to, info.reason, info.actor_peer_id
        )

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
