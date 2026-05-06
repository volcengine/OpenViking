# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stdlib HS256 JWT signer/verifier for OAuth 2.1 access tokens.

Avoids adding a third-party JWT dependency — the surface area we need (one
algorithm, one issuer, one audience check) is small enough to implement
correctly against RFC 7519. Only HS256 is supported; alg='none' is rejected.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HEADER = {"alg": "HS256", "typ": "at+jwt"}
_HEADER_BYTES = json.dumps(_HEADER, separators=(",", ":"), sort_keys=True).encode("utf-8")


class OAuthInvalidTokenError(Exception):
    """Raised when a token cannot be verified (bad signature, expired, malformed)."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except (binascii.Error, ValueError) as exc:
        raise OAuthInvalidTokenError(f"Invalid base64url segment: {exc}") from exc


class JwtSigner:
    """HS256 JWT signer/verifier.

    Tokens contain exp/iat/jti automatically; callers supply the rest of the
    claim set (iss, sub, aud, role, account_id, user_id, scope, client_id, ...).
    """

    def __init__(self, secret: bytes) -> None:
        if not isinstance(secret, (bytes, bytearray)) or len(secret) < 32:
            raise ValueError("JWT signing secret must be at least 32 bytes")
        self._secret = bytes(secret)

    def sign(self, claims: dict[str, Any], ttl_seconds: int) -> str:
        """Sign a JWT with the given claims and TTL.

        Adds `iat`, `exp`, and a random `jti` if not already present.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = int(time.time())
        payload = dict(claims)
        payload.setdefault("iat", now)
        payload.setdefault("exp", now + ttl_seconds)
        payload.setdefault("jti", secrets.token_urlsafe(12))

        header_b64 = _b64url_encode(_HEADER_BYTES)
        payload_b64 = _b64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        sig_b64 = _b64url_encode(signature)
        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def verify(
        self,
        token: str,
        *,
        expected_iss: Optional[str] = None,
        expected_aud: Optional[str] = None,
        leeway_seconds: int = 30,
    ) -> dict[str, Any]:
        """Validate signature, expiry, issuer/audience and return the claim set.

        - `leeway_seconds` accommodates minor clock skew on `exp` and `nbf`.
        - `expected_aud` is enforced only if both the token has an `aud` claim
          AND the caller provides a value, matching the OAuth 2.1 + RFC 8707
          recommendation that `resource` is SHOULD-not-MUST.
        - `expected_iss`, when provided, must match exactly.
        """
        if not isinstance(token, str):
            raise OAuthInvalidTokenError("Token must be a string")
        parts = token.split(".")
        if len(parts) != 3:
            raise OAuthInvalidTokenError("Token must have 3 segments")

        header_b64, payload_b64, sig_b64 = parts
        try:
            header = json.loads(_b64url_decode(header_b64))
        except (json.JSONDecodeError, OAuthInvalidTokenError, UnicodeDecodeError) as exc:
            raise OAuthInvalidTokenError(f"Invalid header: {exc}") from exc
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise OAuthInvalidTokenError(
                f"Unsupported alg: {header.get('alg') if isinstance(header, dict) else '<n/a>'}"
            )

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        try:
            actual_sig = _b64url_decode(sig_b64)
        except OAuthInvalidTokenError:
            raise
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise OAuthInvalidTokenError("Bad signature")

        try:
            payload = json.loads(_b64url_decode(payload_b64))
        except (json.JSONDecodeError, OAuthInvalidTokenError, UnicodeDecodeError) as exc:
            raise OAuthInvalidTokenError(f"Invalid payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise OAuthInvalidTokenError("Payload must be a JSON object")

        now = int(time.time())
        exp = payload.get("exp")
        if not isinstance(exp, int) or now > exp + leeway_seconds:
            raise OAuthInvalidTokenError("Token expired")
        nbf = payload.get("nbf")
        if isinstance(nbf, int) and now + leeway_seconds < nbf:
            raise OAuthInvalidTokenError("Token not yet valid")

        if expected_iss is not None and payload.get("iss") != expected_iss:
            raise OAuthInvalidTokenError("Issuer mismatch")

        if expected_aud is not None and "aud" in payload:
            aud = payload["aud"]
            if isinstance(aud, str):
                if aud != expected_aud:
                    raise OAuthInvalidTokenError("Audience mismatch")
            elif isinstance(aud, list):
                if expected_aud not in aud:
                    raise OAuthInvalidTokenError("Audience mismatch")
            else:
                raise OAuthInvalidTokenError("Invalid aud claim")

        return payload


def looks_like_jwt(token: str) -> bool:
    """Cheap structural check used to discriminate JWT from API key bearers.

    Returns True only if the token has 3 segments and the header segment
    base64url-decodes to a JSON object containing an `alg` field. False on
    any deviation — the caller should then treat the bearer as an API key.
    """
    if not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return False
    try:
        header = json.loads(_b64url_decode(parts[0]))
    except (OAuthInvalidTokenError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(header, dict) and "alg" in header


def load_or_generate_secret(
    workspace: Path,
    override_b64: Optional[str] = None,
    *,
    filename: str = "oauth_jwt.key",
) -> bytes:
    """Resolve the HMAC signing secret.

    Priority: explicit base64 override → on-disk persisted key → fresh random
    32-byte key written to `{workspace}/{filename}` with mode 0600.
    """
    if override_b64:
        try:
            secret = base64.b64decode(override_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"Invalid oauth.signing_key_b64: {exc}") from exc
        if len(secret) < 32:
            raise ValueError("oauth.signing_key_b64 must decode to at least 32 bytes")
        return secret

    key_path = Path(workspace).expanduser() / filename
    if key_path.exists():
        secret = key_path.read_bytes()
        if len(secret) < 32:
            logger.warning(
                "OAuth JWT key at %s is shorter than 32 bytes; regenerating.", key_path
            )
        else:
            return secret

    workspace_path = Path(workspace).expanduser()
    workspace_path.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    # Write with 0600 atomically: write then chmod (umask-agnostic).
    tmp = key_path.with_suffix(key_path.suffix + ".tmp")
    tmp.write_bytes(secret)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        # Best-effort on filesystems that don't support chmod (Windows, some FUSE).
        logger.debug("Could not chmod %s to 0600", tmp)
    os.replace(tmp, key_path)
    logger.info("Generated new OAuth JWT signing key at %s", key_path)
    return secret
