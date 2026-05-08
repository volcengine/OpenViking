# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OTP and short-code utilities for the OAuth 2.1 Phase-1 OTP grant."""

from __future__ import annotations

import hashlib
import secrets

# 32-character alphabet (24 letters + 8 digits) with visually ambiguous
# glyphs removed: O / 0 / I / 1. Used for the human-typed OTPs.
_OTP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
OTP_LENGTH = 6


def generate_otp(length: int = OTP_LENGTH) -> str:
    """Generate a cryptographically random OTP from the unambiguous alphabet."""
    if length < 4:
        raise ValueError("OTP length must be at least 4")
    return "".join(secrets.choice(_OTP_ALPHABET) for _ in range(length))


def hash_secret(plain: str) -> str:
    """SHA-256 hex digest used to index OTPs / auth codes / refresh tokens.

    All three are high-entropy random tokens (≥ 128 bits), so a fast unsalted
    digest is sufficient — Argon2 is reserved for low-entropy user passwords.
    """
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
