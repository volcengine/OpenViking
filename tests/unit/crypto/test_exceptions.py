# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for crypto exception classes.
"""

from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    ConfigError,
    CorruptedCiphertextError,
    EncryptionError,
    InvalidMagicError,
    KeyMismatchError,
    KeyNotFoundError,
    KMSError,
)


def test_encryption_error_inheritance():
    """Test all exception classes inherit from EncryptionError."""
    assert issubclass(InvalidMagicError, EncryptionError)
    assert issubclass(CorruptedCiphertextError, EncryptionError)
    assert issubclass(AuthenticationFailedError, EncryptionError)
    assert issubclass(KeyMismatchError, EncryptionError)
    assert issubclass(KeyNotFoundError, EncryptionError)
    assert issubclass(ConfigError, EncryptionError)
    assert issubclass(KMSError, EncryptionError)


def test_exception_instantiation():
    """Test exception classes can be instantiated correctly."""
    err = EncryptionError("test error")
    assert str(err) == "test error"

    err = InvalidMagicError("invalid magic")
    assert str(err) == "invalid magic"

    err = CorruptedCiphertextError("ciphertext corrupted")
    assert str(err) == "ciphertext corrupted"

    err = AuthenticationFailedError("auth failed")
    assert str(err) == "auth failed"

    err = KeyMismatchError("key mismatch")
    assert str(err) == "key mismatch"

    err = KeyNotFoundError("key not found")
    assert str(err) == "key not found"

    err = ConfigError("config error")
    assert str(err) == "config error"

    err = KMSError("kms error")
    assert str(err) == "kms error"
