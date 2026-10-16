# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Short-lived, single-use tokens for downloading one file through MCP.

Tokens are process-local and are lost on restart. The MCP call and subsequent
download must reach the same worker. Only the file URI and issuing identity are
retained; credentials, group memberships, and ACL bypass flags are never stored.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from openviking.server.identity import RequestContext, Role


class DownloadTokenError(Exception):
    """A download token is missing, unknown, already consumed, or expired."""


@dataclass(frozen=True)
class DownloadToken:
    uri: str
    account_id: str
    user_id: str
    role: Role
    actor_peer_id: str | None
    from_oauth: bool
    expires_at: float


class DownloadTokenStore:
    def __init__(self) -> None:
        self._store: dict[str, DownloadToken] = {}

    def issue(self, uri: str, ctx: RequestContext, ttl_seconds: int = 600) -> tuple[str, float]:
        """Bind one download to the caller identity and return (token, expiry)."""
        now = time.time()
        self._purge_expired(now)
        expires_at = now + max(1, ttl_seconds)
        record = DownloadToken(
            uri=uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=ctx.role,
            actor_peer_id=ctx.actor_peer_id,
            from_oauth=ctx.from_oauth,
            expires_at=expires_at,
        )
        for _ in range(8):
            token = secrets.token_urlsafe(32)
            if token not in self._store:
                self._store[token] = record
                return token, expires_at
        raise RuntimeError("download_token_store: failed to mint a unique token")

    def consume(self, token: str) -> DownloadToken:
        """Burn the token before validating expiry, preventing concurrent replays."""
        if not token:
            raise DownloadTokenError("missing download token")
        record = self._store.pop(token, None)
        if record is None:
            raise DownloadTokenError("unknown or already-consumed download token")
        if record.expires_at <= time.time():
            raise DownloadTokenError("download token expired")
        return record

    def _purge_expired(self, now: float) -> None:
        for token, record in list(self._store.items()):
            if record.expires_at <= now:
                self._store.pop(token, None)


download_token_store = DownloadTokenStore()
