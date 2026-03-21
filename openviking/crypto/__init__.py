# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking Encryption Module

Provides multi-tenant encryption functionality, including:
- Envelope Encryption
- Multiple key providers (Local File, Vault, Volcengine KMS)
- API Key hashing storage (Argon2id)
"""

from openviking.crypto.config import (
    bootstrap_encryption,
    validate_encryption_config,
)
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    CorruptedCiphertextError,
    EncryptionError,
    InvalidMagicError,
    KeyMismatchError,
)
from openviking.crypto.providers import (
    LocalFileProvider,
    RootKeyProvider,
    VaultProvider,
    VolcengineKMSProvider,
    create_root_key_provider,
)

__all__ = [
    "RootKeyProvider",
    "LocalFileProvider",
    "VaultProvider",
    "VolcengineKMSProvider",
    "create_root_key_provider",
    "FileEncryptor",
    "validate_encryption_config",
    "bootstrap_encryption",
    "EncryptionError",
    "InvalidMagicError",
    "CorruptedCiphertextError",
    "AuthenticationFailedError",
    "KeyMismatchError",
]
