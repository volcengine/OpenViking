# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Encryption module exception definitions.
"""


class EncryptionError(Exception):
    """Base class for encryption-related errors."""

    pass


class InvalidMagicError(EncryptionError):
    """Invalid magic number error."""

    pass


class CorruptedCiphertextError(EncryptionError):
    """Corrupted ciphertext error."""

    pass


class AuthenticationFailedError(EncryptionError):
    """Authentication tag verification failed error."""

    pass


class KeyMismatchError(EncryptionError):
    """Key mismatch error."""

    pass


class KeyNotFoundError(EncryptionError):
    """Key not found error."""

    pass


class ConfigError(EncryptionError):
    """Configuration error."""

    pass


class KMSError(EncryptionError):
    """KMS service error."""

    pass
