# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite-backed OAuth state storage.

Holds three tables — DCR clients, short-lived codes (auth codes + OTPs share
this table, distinguished by ``kind``), and refresh tokens. Uses stdlib
``sqlite3`` wrapped in ``asyncio.to_thread`` for async ergonomics; Phase 1
QPS doesn't justify the new ``aiosqlite`` dependency.

Concurrency: a single ``sqlite3.Connection`` is shared across worker threads
(``check_same_thread=False``). Every DB access — both reads and writes —
serializes through ``self._lock``, because the stdlib connection's cursor
state is not safe for concurrent use across threads. With ``isolation_level=
None`` each statement is autocommitted, so the lock granularity is per-call.

Atomicity guarantee: one-shot consumption (OTP / auth code / refresh) is done
via a single ``UPDATE ... WHERE used = 0 RETURNING ...`` so that two concurrent
consumers cannot both succeed — the loser sees zero rows and is rejected.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from openviking.server.oauth.otp import hash_secret
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_secret_hash TEXT,
    redirect_uris TEXT NOT NULL,                      -- JSON array
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    grant_types TEXT NOT NULL DEFAULT '["authorization_code","refresh_token"]',
    response_types TEXT NOT NULL DEFAULT '["code"]',
    client_name TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('otp', 'code')),
    client_id TEXT,
    redirect_uri TEXT,
    code_challenge TEXT,
    code_challenge_method TEXT,
    scope TEXT,
    resource TEXT,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    -- SHA-256 hex of the API key value that authorized this row. Recorded at
    -- OTP issuance / oauth-verify time and copied forward into refresh/access
    -- so that key rotation or deletion invalidates every derived OAuth token.
    -- Nullable only for backwards compat with rows written before this field
    -- existed; auth path treats NULL as fail-closed.
    authorizing_key_fp TEXT,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires ON oauth_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_user ON oauth_codes(account_id, user_id);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    scope TEXT,
    resource TEXT,
    authorizing_key_fp TEXT,  -- see oauth_codes for semantics
    expires_at INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    replaced_by TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_expires ON oauth_refresh_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_user ON oauth_refresh_tokens(account_id, user_id);

CREATE TABLE IF NOT EXISTS oauth_pending_authorizations (
    pending_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly INTEGER NOT NULL,
    code_challenge TEXT NOT NULL,
    scopes TEXT,
    resource TEXT,
    state TEXT,
    display_code TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    verified_account_id TEXT,
    verified_user_id TEXT,
    verified_role TEXT,
    -- Recorded at oauth-verify time from the verifier's API key; copied
    -- into the auth code on mint, then into refresh/access on exchange.
    verified_key_fp TEXT,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_pending_expires ON oauth_pending_authorizations(expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_pending_display ON oauth_pending_authorizations(display_code);

CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    scope TEXT,
    resource TEXT,
    authorizing_key_fp TEXT,  -- see oauth_codes for semantics
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_access_expires ON oauth_access_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_access_user ON oauth_access_tokens(account_id, user_id);
"""


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class OAuthStore:
    """Async wrapper around a single sqlite3 connection (WAL mode)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            isolation_level=None,  # autocommit; we manage transactions explicitly
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        self._migrate(conn)
        self._conn = conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Idempotent column additions for dev DBs that predate a field.

        ``CREATE TABLE IF NOT EXISTS`` does not retrofit columns onto an
        existing table, so for any field added after the original schema we
        ALTER TABLE ADD COLUMN guarded by a ``PRAGMA table_info`` check.
        """
        column_additions = (
            ("oauth_codes", "authorizing_key_fp", "TEXT"),
            ("oauth_pending_authorizations", "verified_key_fp", "TEXT"),
            ("oauth_refresh_tokens", "authorizing_key_fp", "TEXT"),
            ("oauth_access_tokens", "authorizing_key_fp", "TEXT"),
        )
        for table, column, decl in column_additions:
            cur = conn.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---- DCR ----

    async def register_client(
        self,
        *,
        client_id: Optional[str] = None,
        redirect_uris: list[str],
        client_name: Optional[str] = None,
        token_endpoint_auth_method: str = "none",
        grant_types: Optional[list[str]] = None,
        response_types: Optional[list[str]] = None,
        client_secret: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a freshly registered client and return the public record.

        ``client_id`` may be supplied by the caller (e.g. the MCP SDK's
        RegistrationHandler generates one via uuid4); otherwise we mint a
        URL-safe random ID for in-process callers.
        """
        if not redirect_uris:
            raise ValueError("redirect_uris must be non-empty")
        if client_id is None:
            client_id = secrets.token_urlsafe(16)
        secret_hash = hash_secret(client_secret) if client_secret else None
        now = int(time.time())
        record = {
            "client_id": client_id,
            "client_secret_hash": secret_hash,
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "grant_types": grant_types or ["authorization_code", "refresh_token"],
            "response_types": response_types or ["code"],
            "client_name": client_name,
            "created_at": now,
        }

        def _insert() -> None:
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO oauth_clients "
                "(client_id, client_secret_hash, redirect_uris, "
                "token_endpoint_auth_method, grant_types, response_types, "
                "client_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["client_id"],
                    record["client_secret_hash"],
                    json.dumps(record["redirect_uris"]),
                    record["token_endpoint_auth_method"],
                    json.dumps(record["grant_types"]),
                    json.dumps(record["response_types"]),
                    record["client_name"],
                    record["created_at"],
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_insert)
        return record

    async def get_client(self, client_id: str) -> Optional[dict[str, Any]]:
        def _query() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            record = _row_to_dict(cur, row)
            record["redirect_uris"] = json.loads(record["redirect_uris"])
            record["grant_types"] = json.loads(record["grant_types"])
            record["response_types"] = json.loads(record["response_types"])
            return record

        async with self._lock:
            return await asyncio.to_thread(_query)

    # ---- OTPs ----

    async def insert_otp(
        self,
        *,
        otp_plain: str,
        account_id: str,
        user_id: str,
        role: str,
        authorizing_key_fp: str,
        ttl_seconds: int,
    ) -> int:
        """Insert an OTP row and return its expires_at timestamp.

        ``authorizing_key_fp`` is the SHA-256 fingerprint of the API key the
        caller presented when requesting the OTP. It is propagated forward to
        any auth code / access / refresh token derived from this OTP so that
        rotating or removing the key invalidates the entire downstream chain.
        """
        now = int(time.time())
        expires_at = now + ttl_seconds

        def _insert() -> None:
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO oauth_codes "
                "(code_hash, kind, account_id, user_id, role, "
                "authorizing_key_fp, expires_at, created_at) "
                "VALUES (?, 'otp', ?, ?, ?, ?, ?, ?)",
                (
                    hash_secret(otp_plain),
                    account_id,
                    user_id,
                    role,
                    authorizing_key_fp,
                    expires_at,
                    now,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_insert)
        return expires_at

    async def consume_otp(self, otp_plain: str) -> Optional[dict[str, Any]]:
        """Atomically mark an OTP used and return its identity claims, or None."""
        return await self._atomic_consume_code(otp_plain, expected_kind="otp")

    # ---- Auth codes ----

    async def insert_auth_code(
        self,
        *,
        code_plain: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: Optional[str],
        resource: Optional[str],
        account_id: str,
        user_id: str,
        role: str,
        authorizing_key_fp: str,
        ttl_seconds: int,
    ) -> int:
        now = int(time.time())
        expires_at = now + ttl_seconds

        def _insert() -> None:
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO oauth_codes "
                "(code_hash, kind, client_id, redirect_uri, code_challenge, "
                "code_challenge_method, scope, resource, account_id, user_id, "
                "role, authorizing_key_fp, expires_at, created_at) "
                "VALUES (?, 'code', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_secret(code_plain),
                    client_id,
                    redirect_uri,
                    code_challenge,
                    code_challenge_method,
                    scope,
                    resource,
                    account_id,
                    user_id,
                    role,
                    authorizing_key_fp,
                    expires_at,
                    now,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_insert)
        return expires_at

    async def consume_auth_code(self, code_plain: str) -> Optional[dict[str, Any]]:
        return await self._atomic_consume_code(code_plain, expected_kind="code")

    async def peek_auth_code(self, code_plain: str) -> Optional[dict[str, Any]]:
        """Non-destructive lookup of an unused, unexpired auth code.

        Used by the SDK to inspect the code (e.g. read code_challenge for PKCE)
        before deciding whether to exchange. Pairs with ``consume_auth_code``,
        which is the only one-shot operation.
        """
        return await self._peek_code(code_plain, expected_kind="code")

    async def _peek_code(self, plain: str, *, expected_kind: str) -> Optional[dict[str, Any]]:
        code_hash = hash_secret(plain)
        now = int(time.time())

        def _q() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT client_id, redirect_uri, code_challenge, "
                "code_challenge_method, scope, resource, account_id, user_id, role, "
                "authorizing_key_fp, expires_at, created_at FROM oauth_codes "
                "WHERE code_hash = ? AND kind = ? AND used = 0 AND expires_at > ?",
                (code_hash, expected_kind, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(cur, row)

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def _atomic_consume_code(
        self, plain: str, *, expected_kind: str
    ) -> Optional[dict[str, Any]]:
        code_hash = hash_secret(plain)
        now = int(time.time())

        def _consume() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "UPDATE oauth_codes SET used = 1 "
                "WHERE code_hash = ? AND kind = ? AND used = 0 AND expires_at > ? "
                "RETURNING client_id, redirect_uri, code_challenge, "
                "code_challenge_method, scope, resource, account_id, user_id, role, "
                "authorizing_key_fp, expires_at, created_at",
                (code_hash, expected_kind, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(cur, row)

        async with self._lock:
            return await asyncio.to_thread(_consume)

    # ---- Refresh tokens ----

    async def insert_refresh(
        self,
        *,
        token_plain: str,
        client_id: str,
        account_id: str,
        user_id: str,
        role: str,
        scope: Optional[str],
        resource: Optional[str],
        authorizing_key_fp: str,
        ttl_seconds: int,
    ) -> int:
        now = int(time.time())
        expires_at = now + ttl_seconds

        def _insert() -> None:
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO oauth_refresh_tokens "
                "(token_hash, client_id, account_id, user_id, role, scope, "
                "resource, authorizing_key_fp, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_secret(token_plain),
                    client_id,
                    account_id,
                    user_id,
                    role,
                    scope,
                    resource,
                    authorizing_key_fp,
                    expires_at,
                    now,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_insert)
        return expires_at

    async def peek_refresh(self, token_plain: str) -> Optional[dict[str, Any]]:
        """Non-destructive lookup of a still-active refresh token."""
        token_hash = hash_secret(token_plain)
        now = int(time.time())

        def _q() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT client_id, account_id, user_id, role, scope, resource, "
                "authorizing_key_fp, expires_at, created_at FROM oauth_refresh_tokens "
                "WHERE token_hash = ? AND consumed = 0 AND expires_at > ?",
                (token_hash, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(cur, row)

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def consume_refresh(
        self,
        *,
        token_plain: str,
        replaced_by_plain: Optional[str],
    ) -> Optional[dict[str, Any]]:
        """Mark refresh token consumed (and link to its replacement) atomically.

        Returns the original token's identity/scope claims, or None if the
        token is unknown/expired/already consumed. Reuse detection: callers
        that get None for a previously-known token MUST call ``revoke_chain``
        to invalidate the entire family — see RFC 9700 §4.14.
        """
        token_hash = hash_secret(token_plain)
        replaced_hash = hash_secret(replaced_by_plain) if replaced_by_plain else None
        now = int(time.time())

        def _consume() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "UPDATE oauth_refresh_tokens SET consumed = 1, replaced_by = ? "
                "WHERE token_hash = ? AND consumed = 0 AND expires_at > ? "
                "RETURNING client_id, account_id, user_id, role, scope, resource, "
                "authorizing_key_fp, expires_at, created_at",
                (replaced_hash, token_hash, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(cur, row)

        async with self._lock:
            return await asyncio.to_thread(_consume)

    async def is_refresh_known_but_consumed(self, token_plain: str) -> bool:
        """Return True if the token exists and has already been consumed (replay)."""
        token_hash = hash_secret(token_plain)

        def _q() -> bool:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT consumed FROM oauth_refresh_tokens WHERE token_hash = ?",
                (token_hash,),
            )
            row = cur.fetchone()
            return bool(row and row[0])

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def revoke_chain(self, *, client_id: str, account_id: str, user_id: str) -> int:
        """Revoke every refresh token for (client, account, user). Returns count."""

        def _revoke() -> int:
            assert self._conn is not None
            cur = self._conn.execute(
                "UPDATE oauth_refresh_tokens SET consumed = 1 "
                "WHERE client_id = ? AND account_id = ? AND user_id = ? AND consumed = 0",
                (client_id, account_id, user_id),
            )
            return cur.rowcount

        async with self._lock:
            return await asyncio.to_thread(_revoke)

    # ---- Access tokens ----

    async def insert_access(
        self,
        *,
        token_plain: str,
        client_id: str,
        account_id: str,
        user_id: str,
        role: str,
        scope: Optional[str],
        resource: Optional[str],
        authorizing_key_fp: str,
        ttl_seconds: int,
    ) -> int:
        now = int(time.time())
        expires_at = now + ttl_seconds

        def _insert() -> None:
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO oauth_access_tokens "
                "(token_hash, client_id, account_id, user_id, role, scope, "
                "resource, authorizing_key_fp, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_secret(token_plain),
                    client_id,
                    account_id,
                    user_id,
                    role,
                    scope,
                    resource,
                    authorizing_key_fp,
                    expires_at,
                    now,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_insert)
        return expires_at

    async def load_access(self, token_plain: str) -> Optional[dict[str, Any]]:
        """Return the access token's claims dict, or None if invalid/expired/revoked."""
        token_hash = hash_secret(token_plain)
        now = int(time.time())

        def _q() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT client_id, account_id, user_id, role, scope, resource, "
                "authorizing_key_fp, expires_at, created_at FROM oauth_access_tokens "
                "WHERE token_hash = ? AND revoked = 0 AND expires_at > ?",
                (token_hash, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(cur, row)

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def revoke_access(self, token_plain: str) -> bool:
        token_hash = hash_secret(token_plain)

        def _revoke() -> bool:
            assert self._conn is not None
            cur = self._conn.execute(
                "UPDATE oauth_access_tokens SET revoked = 1 WHERE token_hash = ? AND revoked = 0",
                (token_hash,),
            )
            return cur.rowcount > 0

        async with self._lock:
            return await asyncio.to_thread(_revoke)

    async def revoke_user_tokens(self, *, account_id: str, user_id: str) -> dict[str, int]:
        """Revoke all access + refresh tokens for a (account, user) pair.

        Used when an API key for that user is rotated / deleted: every OAuth
        token derived from that user identity gets invalidated. Auth codes
        and OTPs for the same user are also wiped to prevent in-flight
        completion of an exchange started before the revocation.
        """

        def _revoke() -> dict[str, int]:
            assert self._conn is not None
            access = self._conn.execute(
                "UPDATE oauth_access_tokens SET revoked = 1 "
                "WHERE account_id = ? AND user_id = ? AND revoked = 0",
                (account_id, user_id),
            ).rowcount
            refresh = self._conn.execute(
                "UPDATE oauth_refresh_tokens SET consumed = 1 "
                "WHERE account_id = ? AND user_id = ? AND consumed = 0",
                (account_id, user_id),
            ).rowcount
            codes = self._conn.execute(
                "UPDATE oauth_codes SET used = 1 WHERE account_id = ? AND user_id = ? AND used = 0",
                (account_id, user_id),
            ).rowcount
            return {
                "access_tokens_revoked": access,
                "refresh_tokens_revoked": refresh,
                "codes_revoked": codes,
            }

        async with self._lock:
            return await asyncio.to_thread(_revoke)

    # ---- Pending authorizations (carry OAuth params across the OTP page) ----

    async def create_pending_authorization(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        redirect_uri_provided_explicitly: bool,
        code_challenge: str,
        scopes: Optional[list[str]],
        resource: Optional[str],
        state: Optional[str],
        display_code: str,
        ttl_seconds: int = 600,
    ) -> str:
        """Stash the AuthorizationParams under a fresh pending_id and return it.

        ``display_code`` is the human-readable verification code that will be
        shown on the authorize page; the user re-types it into the console
        verify form and the server resolves the pending row by it.
        """
        pending_id = secrets.token_urlsafe(16)
        now = int(time.time())

        def _insert() -> None:
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO oauth_pending_authorizations "
                "(pending_id, client_id, redirect_uri, redirect_uri_provided_explicitly, "
                "code_challenge, scopes, resource, state, display_code, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pending_id,
                    client_id,
                    redirect_uri,
                    int(redirect_uri_provided_explicitly),
                    code_challenge,
                    json.dumps(scopes) if scopes else None,
                    resource,
                    state,
                    display_code,
                    now + ttl_seconds,
                    now,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_insert)
        return pending_id

    async def load_pending_authorization(self, pending_id: str) -> Optional[dict[str, Any]]:
        now = int(time.time())

        def _q() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT * FROM oauth_pending_authorizations "
                "WHERE pending_id = ? AND expires_at > ?",
                (pending_id, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            record = _row_to_dict(cur, row)
            record["redirect_uri_provided_explicitly"] = bool(
                record["redirect_uri_provided_explicitly"]
            )
            record["verified"] = bool(record.get("verified", 0))
            record["scopes"] = json.loads(record["scopes"]) if record["scopes"] else None
            return record

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def find_pending_by_display_code(self, display_code: str) -> Optional[dict[str, Any]]:
        """Return the pending row whose display_code matches, or None."""
        if not display_code:
            return None
        normalized = display_code.strip().upper()
        now = int(time.time())

        def _q() -> Optional[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT * FROM oauth_pending_authorizations "
                "WHERE display_code = ? AND expires_at > ? AND verified = 0",
                (normalized, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            record = _row_to_dict(cur, row)
            record["redirect_uri_provided_explicitly"] = bool(
                record["redirect_uri_provided_explicitly"]
            )
            record["verified"] = bool(record.get("verified", 0))
            record["scopes"] = json.loads(record["scopes"]) if record["scopes"] else None
            return record

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def mark_pending_verified(
        self,
        *,
        pending_id: str,
        account_id: str,
        user_id: str,
        role: str,
        verified_key_fp: str,
    ) -> bool:
        """Atomically mark a pending authorization as verified and bind identity.

        ``verified_key_fp`` is the SHA-256 fingerprint of the API key behind
        the verifying request — every OAuth token derived from this pending
        row inherits it for lifecycle binding.

        Returns True on success, False if the row is missing, expired, or
        already verified (one-shot).
        """
        now = int(time.time())

        def _u() -> bool:
            assert self._conn is not None
            cur = self._conn.execute(
                "UPDATE oauth_pending_authorizations SET verified = 1, "
                "verified_account_id = ?, verified_user_id = ?, verified_role = ?, "
                "verified_key_fp = ? "
                "WHERE pending_id = ? AND verified = 0 AND expires_at > ?",
                (account_id, user_id, role, verified_key_fp, pending_id, now),
            )
            return cur.rowcount > 0

        async with self._lock:
            return await asyncio.to_thread(_u)

    async def delete_pending_authorization(self, pending_id: str) -> None:
        def _del() -> None:
            assert self._conn is not None
            self._conn.execute(
                "DELETE FROM oauth_pending_authorizations WHERE pending_id = ?",
                (pending_id,),
            )

        async with self._lock:
            await asyncio.to_thread(_del)

    # ---- Maintenance ----

    async def gc_expired(self) -> dict[str, int]:
        now = int(time.time())

        def _gc() -> dict[str, int]:
            assert self._conn is not None
            codes = self._conn.execute(
                "DELETE FROM oauth_codes WHERE expires_at < ? OR used = 1", (now,)
            ).rowcount
            # Refresh tombstones (consumed=1) are KEPT until the row's natural
            # expires_at. Replay detection (RFC 9700 §4.14) depends on being
            # able to distinguish "I've seen this token, it was consumed" from
            # "never seen this token" — deleting tombstones early collapses
            # those two cases and silently breaks family revocation. Storage
            # cost is bounded by the refresh TTL (default 30d).
            refreshes = self._conn.execute(
                "DELETE FROM oauth_refresh_tokens WHERE expires_at < ?",
                (now,),
            ).rowcount
            access = self._conn.execute(
                "DELETE FROM oauth_access_tokens WHERE expires_at < ? OR revoked = 1",
                (now,),
            ).rowcount
            pending = self._conn.execute(
                "DELETE FROM oauth_pending_authorizations WHERE expires_at < ?",
                (now,),
            ).rowcount
            return {
                "codes_deleted": codes,
                "refresh_tokens_deleted": refreshes,
                "access_tokens_deleted": access,
                "pending_authorizations_deleted": pending,
            }

        async with self._lock:
            return await asyncio.to_thread(_gc)

    # ---- Test helpers (no-op in production paths) ----

    async def _all_codes_for_test(self) -> Iterable[dict[str, Any]]:
        def _q() -> list[dict[str, Any]]:
            assert self._conn is not None
            cur = self._conn.execute("SELECT * FROM oauth_codes")
            return [_row_to_dict(cur, r) for r in cur.fetchall()]

        async with self._lock:
            return await asyncio.to_thread(_q)
