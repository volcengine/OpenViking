# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Short-lived upload tokens used by the MCP progressive upload flow.

Issued by the MCP ``add_resource`` tool when a caller passes a local-file path; consumed by
``POST /api/v1/resources/temp_upload`` when the request carries a ``?token=`` instead of an
API key. Tokens are 10-character base62 strings indexed in a process-local dict — no signing
key, no on-disk state, no replay-set bookkeeping. ``dict.pop`` doubles as the
consume-and-burn primitive.

The token carries the identity bound at issue time (account/user/role and OAuth origin),
the caller's actor peer scope (``actor_peer_id``), and the business params
(``to``/``parent``/``reason``/``parse_mode``) so the server can
finish ingestion automatically once the file lands — the caller does not re-invoke
``add_resource``, and the ingest keeps the original peer scope. Tokens minted by the MCP
``add_skill`` tool carry ``kind="skill"`` plus the skill install params instead, and the upload
is installed as skills rather than ingested as a resource. The ``temp_file_id`` is minted by
:class:`openviking.server.temp_upload_store.TempUploadStore` at upload time, so the token
does not pre-bind a filename and the upload can flow through either the local or shared
TempUploadStore mode without a side-channel.

``download_token_store`` is a separate instance used by the MCP ``download`` tool: its tokens
carry the resolved ``uri`` of one file and are consumed by ``GET /api/v1/content/download``.
Keeping the two stores apart means an upload token can never be redeemed for a download, or
the reverse.

By default tokens are process-local and lost on restart. An opt-in Redis transfer-token
backend shares only this handshake; file payload storage remains a separate owner.
"""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.server.identity import Role
from openviking.storage.acl import AclSpec

if TYPE_CHECKING:
    from openviking.server.transfer_token_backend import RedisTransferTokens

_TOKEN_ALPHABET = string.ascii_letters + string.digits  # base62
_TOKEN_LENGTH = 10


class UploadTokenError(Exception):
    """Raised when an upload token is unknown or expired."""


@dataclass(frozen=True)
class _TokenInfo:
    account_id: str
    user_id: str
    role: Role
    to: str
    parent: str
    reason: str
    actor_peer_id: str
    processing_mode: ProcessingMode
    tags: Optional[list[str]]
    tag_mode: str
    parse_mode: str
    expires_at: float
    kind: str = "resource"
    skill_target_uri: str = ""
    skill_names: Optional[list[str]] = None
    list_only: bool = False
    acl: AclSpec | None = None
    from_oauth: bool = False
    uri: str = ""


@dataclass(frozen=True)
class ConsumedUploadToken:
    """Identity and business params bound to an upload token, returned by ``consume``."""

    account_id: str
    user_id: str
    role: Role
    to: str
    reason: str
    actor_peer_id: str
    parent: str = ""
    processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE
    tags: Optional[list[str]] = None
    tag_mode: str = "replace"
    parse_mode: str = "default"
    kind: str = "resource"
    skill_target_uri: str = ""
    skill_names: Optional[list[str]] = None
    list_only: bool = False
    acl: AclSpec | None = None
    from_oauth: bool = False
    uri: str = ""


def _new_token():
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


class UploadTokenStore:
    def __init__(self, purpose="upload") -> None:
        self._store: dict[str, _TokenInfo] = {}
        self.purpose = purpose
        self._backend: RedisTransferTokens | None = None

    def _new_info(
        self,
        account_id: str,
        user_id: str,
        ttl_seconds: int,
        *,
        role: Role,
        to: str = "",
        parent: str = "",
        reason: str = "",
        actor_peer_id: str = "",
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        tags: Optional[list[str]] = None,
        tag_mode: str = "replace",
        parse_mode: str = "default",
        kind: str = "resource",
        skill_target_uri: str = "",
        skill_names: Optional[list[str]] = None,
        list_only: bool = False,
        acl: AclSpec | None = None,
        from_oauth: bool = False,
        uri: str = "",
    ) -> _TokenInfo:
        """Mint a fresh token bound to the caller identity and ingestion parameters.

        ``actor_peer_id`` is captured from the minting request's context so server-side
        auto-ingest keeps the caller's peer scope (reason-memory routing) — it is NOT taken
        from the later upload request's headers, which could be spoofed.
        """
        expires_at = time.time() + max(1, ttl_seconds)
        info = _TokenInfo(
            account_id,
            user_id,
            role,
            to,
            parent,
            reason,
            actor_peer_id,
            processing_mode,
            tags,
            tag_mode,
            parse_mode,
            expires_at,
            kind,
            skill_target_uri,
            skill_names,
            list_only,
            acl,
            from_oauth,
            uri,
        )
        return info

    def issue(self, *args, **kwargs) -> Tuple[str, float]:
        if self._backend is not None:
            raise RuntimeError("Shared transfer tokens require aissue")
        self._purge_expired()
        info = self._new_info(*args, **kwargs)
        for _ in range(8):
            token = _new_token()
            if token not in self._store:
                self._store[token] = info
                return token, info.expires_at
        raise RuntimeError("upload_token_store: failed to mint a unique token after 8 attempts")

    async def aissue(self, account_id, user_id, ttl_seconds, **kwargs):
        if self._backend is None:
            return self.issue(account_id, user_id, ttl_seconds, **kwargs)
        info = self._new_info(account_id, user_id, ttl_seconds, **kwargs)
        return await self._backend.issue(self.purpose, info, ttl_seconds)

    async def aconsume(self, token):
        if self._backend is None:
            return self.consume(token)
        if not token:
            raise UploadTokenError("missing upload token")
        info = await self._backend.consume(self.purpose, token)
        if info is None:
            raise UploadTokenError("unknown or already-consumed upload token")
        return self._consumed(info)

    def consume(self, token: str) -> ConsumedUploadToken:
        """Pop the token and validate it. Returns the bound identity + business params."""
        if self._backend is not None:
            raise RuntimeError("Shared transfer tokens require aconsume")
        if not token:
            raise UploadTokenError("missing upload token")
        info = self._store.pop(token, None)
        if info is None:
            raise UploadTokenError("unknown or already-consumed upload token")
        if info.expires_at < time.time():
            raise UploadTokenError("upload token expired")
        return self._consumed(info)

    @staticmethod
    def _consumed(info):
        return ConsumedUploadToken(
            account_id=info.account_id,
            user_id=info.user_id,
            role=info.role,
            to=info.to,
            parent=info.parent,
            reason=info.reason,
            actor_peer_id=info.actor_peer_id,
            processing_mode=info.processing_mode,
            tags=info.tags,
            tag_mode=info.tag_mode,
            parse_mode=info.parse_mode,
            kind=info.kind,
            skill_target_uri=info.skill_target_uri,
            skill_names=info.skill_names,
            list_only=info.list_only,
            acl=info.acl,
            from_oauth=info.from_oauth,
            uri=info.uri,
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
download_token_store = UploadTokenStore("download")
