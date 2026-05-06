# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for openviking/server/oauth/jwt.py."""

from __future__ import annotations

import base64
import json
import time

import pytest

from openviking.server.oauth.jwt import (
    JwtSigner,
    OAuthInvalidTokenError,
    load_or_generate_secret,
    looks_like_jwt,
)

SECRET_A = b"x" * 32
SECRET_B = b"y" * 32


def _make_signer(secret: bytes = SECRET_A) -> JwtSigner:
    return JwtSigner(secret)


def test_secret_too_short_rejected():
    with pytest.raises(ValueError):
        JwtSigner(b"short")


def test_sign_verify_roundtrip():
    signer = _make_signer()
    token = signer.sign({"role": "user", "account_id": "a", "user_id": "u"}, ttl_seconds=60)
    payload = signer.verify(token)
    assert payload["role"] == "user"
    assert payload["account_id"] == "a"
    assert payload["user_id"] == "u"
    assert "exp" in payload and "iat" in payload and "jti" in payload


def test_verify_rejects_expired():
    signer = _make_signer()
    token = signer.sign({"role": "user"}, ttl_seconds=60)
    # Manually craft an expired token by re-signing with backdated claims.
    expired = signer.sign({"role": "user", "exp": int(time.time()) - 3600}, ttl_seconds=60)
    with pytest.raises(OAuthInvalidTokenError, match="expired"):
        signer.verify(expired)
    # Within leeway should still pass.
    payload = signer.verify(token)
    assert payload["role"] == "user"


def test_verify_rejects_tampered_signature():
    signer = _make_signer()
    token = signer.sign({"role": "user"}, ttl_seconds=60)
    head, payload, sig = token.split(".")
    # Flip a bit in the signature.
    bad_sig = list(sig)
    bad_sig[0] = "A" if sig[0] != "A" else "B"
    tampered = f"{head}.{payload}.{''.join(bad_sig)}"
    with pytest.raises(OAuthInvalidTokenError, match="Bad signature"):
        signer.verify(tampered)


def test_verify_rejects_wrong_secret():
    signer_a = JwtSigner(SECRET_A)
    signer_b = JwtSigner(SECRET_B)
    token = signer_a.sign({"role": "user"}, ttl_seconds=60)
    with pytest.raises(OAuthInvalidTokenError, match="Bad signature"):
        signer_b.verify(token)


def test_verify_rejects_alg_none():
    signer = _make_signer()
    # Forge a token with alg=none header.
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"exp": int(time.time()) + 60, "role": "root"}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    forged = f"{header}.{payload}."
    with pytest.raises(OAuthInvalidTokenError):
        signer.verify(forged)


def test_verify_rejects_malformed():
    signer = _make_signer()
    for bad in ["", "not.a.jwt", "a.b", "a.b.c.d", "abc"]:
        with pytest.raises(OAuthInvalidTokenError):
            signer.verify(bad)


def test_aud_match_strict_string():
    signer = _make_signer()
    token = signer.sign({"aud": "https://ov.example.com/mcp"}, ttl_seconds=60)
    signer.verify(token, expected_aud="https://ov.example.com/mcp")
    with pytest.raises(OAuthInvalidTokenError, match="Audience"):
        signer.verify(token, expected_aud="https://other/mcp")


def test_aud_match_list():
    signer = _make_signer()
    token = signer.sign({"aud": ["https://a", "https://b"]}, ttl_seconds=60)
    signer.verify(token, expected_aud="https://b")
    with pytest.raises(OAuthInvalidTokenError, match="Audience"):
        signer.verify(token, expected_aud="https://c")


def test_aud_omitted_passes_when_caller_passes_expected():
    """When token has no aud claim, caller passing expected_aud must NOT fail.

    This matches the OAuth 2.1 + RFC 8707 SHOULD-not-MUST stance and keeps
    Claude Desktop (which currently omits the resource parameter) working.
    """
    signer = _make_signer()
    token = signer.sign({"role": "user"}, ttl_seconds=60)
    signer.verify(token, expected_aud="https://anything")


def test_iss_match_strict():
    signer = _make_signer()
    token = signer.sign({"iss": "https://ov.example.com"}, ttl_seconds=60)
    signer.verify(token, expected_iss="https://ov.example.com")
    with pytest.raises(OAuthInvalidTokenError, match="Issuer"):
        signer.verify(token, expected_iss="https://other")


def test_looks_like_jwt_positive():
    signer = _make_signer()
    token = signer.sign({"role": "user"}, ttl_seconds=60)
    assert looks_like_jwt(token) is True


def test_looks_like_jwt_negative_api_keys():
    """Realistic API keys must not be misidentified as JWTs."""
    samples = [
        "ov_root_abcdef0123456789",
        "user-key-no-dots-at-all",
        "key.with.two.but.too.many.dots",
        "almost.jwt.shape",  # only 3 segments but header isn't JSON
        "",
        "abc",
    ]
    for s in samples:
        assert looks_like_jwt(s) is False, s


def test_looks_like_jwt_does_not_raise():
    # Passing arbitrary garbage must never raise.
    assert looks_like_jwt(None) is False  # type: ignore[arg-type]
    assert looks_like_jwt("...") is False
    assert looks_like_jwt("a.b.c") is False  # b64 decodes but not a JSON object


def test_load_or_generate_secret_creates_file(tmp_path):
    secret = load_or_generate_secret(tmp_path)
    key_file = tmp_path / "oauth_jwt.key"
    assert key_file.exists()
    assert key_file.read_bytes() == secret
    assert len(secret) >= 32
    # Second call returns the same secret.
    again = load_or_generate_secret(tmp_path)
    assert again == secret


def test_load_or_generate_secret_override_b64(tmp_path):
    raw = b"k" * 40
    override = base64.b64encode(raw).decode()
    secret = load_or_generate_secret(tmp_path, override_b64=override)
    assert secret == raw
    # Override path should NOT have written a key file.
    assert not (tmp_path / "oauth_jwt.key").exists()


def test_load_or_generate_secret_rejects_short_override(tmp_path):
    too_short = base64.b64encode(b"short").decode()
    with pytest.raises(ValueError):
        load_or_generate_secret(tmp_path, override_b64=too_short)


def test_load_or_generate_secret_rejects_invalid_b64(tmp_path):
    with pytest.raises(ValueError):
        load_or_generate_secret(tmp_path, override_b64="!!!not-b64!!!")
