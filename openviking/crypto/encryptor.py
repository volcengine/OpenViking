# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File encryptor - envelope encryption implementation.

Implements Envelope Encryption pattern:
- Each file has independent random File Key
- File Key is encrypted with Account Key
- Account Key is derived from Root Key
"""

import secrets
import struct
from typing import Tuple

from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    CorruptedCiphertextError,
    InvalidMagicError,
    KeyMismatchError,
)
from openviking.crypto.providers import (
    PROVIDER_LOCAL,
    PROVIDER_VAULT,
    PROVIDER_VOLCENGINE,
    RootKeyProvider,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Magic number: OpenViking Encryption v1
MAGIC = b"OVE1"
MAGIC_LENGTH = len(MAGIC)

# Envelope format version
VERSION = 0x01


class FileEncryptor:
    """File encryptor."""

    def __init__(self, provider: RootKeyProvider):
        """
        Initialize FileEncryptor.

        Args:
            provider: RootKeyProvider instance
        """
        self.provider = provider
        self._provider_type = self._detect_provider_type(provider)

    def _detect_provider_type(self, provider: RootKeyProvider) -> int:
        """Detect Provider type."""
        from openviking.crypto.providers import (
            LocalFileProvider,
            VaultProvider,
            VolcengineKMSProvider,
        )

        if isinstance(provider, LocalFileProvider):
            return PROVIDER_LOCAL
        elif isinstance(provider, VaultProvider):
            return PROVIDER_VAULT
        elif isinstance(provider, VolcengineKMSProvider):
            return PROVIDER_VOLCENGINE
        else:
            raise ValueError(f"Unknown provider type: {type(provider)}")

    async def encrypt(self, account_id: str, plaintext: bytes) -> bytes:
        """
        Encrypt file content.

        Args:
            account_id: Account ID
            plaintext: Plaintext content

        Returns:
            Encrypted content (Envelope format)
        """
        # 1. Generate random File Key
        file_key = secrets.token_bytes(32)

        # 2. Encrypt file content
        data_iv = secrets.token_bytes(12)
        encrypted_content = await self._aes_gcm_encrypt(file_key, data_iv, plaintext)

        # 3. Encrypt File Key (all providers now return (encrypted_key, iv)
        encrypted_file_key, key_iv = await self.provider.encrypt_file_key(file_key, account_id)

        # 4. Build Envelope
        return self._build_envelope(
            self._provider_type,
            encrypted_file_key,
            key_iv,
            data_iv,
            encrypted_content,
        )

    async def decrypt(self, account_id: str, ciphertext: bytes) -> bytes:
        """
        Decrypt file content.

        Args:
            account_id: Account ID
            ciphertext: Ciphertext content

        Returns:
            Decrypted plaintext content
        """
        # 1. Check magic number (check prefix first, before length)
        #    This ensures plaintext files (including empty/short ones) are
        #    returned as-is instead of raising "Ciphertext too short".
        if not ciphertext.startswith(MAGIC):
            # Unencrypted file, return directly
            return ciphertext

        if len(ciphertext) < MAGIC_LENGTH:
            raise InvalidMagicError("Ciphertext too short")

        try:
            # 2. Parse Envelope
            (
                provider_type,
                encrypted_file_key,
                key_iv,
                data_iv,
                encrypted_content,
            ) = self._parse_envelope(ciphertext)
        except Exception as e:
            raise CorruptedCiphertextError(f"Failed to parse envelope: {e}")

        try:
            # 3. Decrypt File Key (all providers now use (encrypted_key, iv, account_id)
            file_key = await self.provider.decrypt_file_key(encrypted_file_key, key_iv, account_id)
        except Exception as e:
            raise KeyMismatchError(f"Failed to decrypt file key: {e}")

        try:
            # 4. Decrypt file content
            return await self._aes_gcm_decrypt(file_key, data_iv, encrypted_content)
        except Exception as e:
            raise AuthenticationFailedError(f"Authentication failed: {e}")

    def _build_envelope(
        self,
        provider_type: int,
        encrypted_file_key: bytes,
        key_iv: bytes,
        data_iv: bytes,
        encrypted_content: bytes,
    ) -> bytes:
        """
        Build Envelope.

        Envelope format:
        - Magic (4B): b"OVE1"
        - Version (1B): 0x01
        - Provider Type (1B)
        - Encrypted File Key Length (2B, big-endian)
        - Key IV Length (2B, big-endian)
        - Data IV Length (2B, big-endian)
        - Encrypted File Key (variable)
        - Key IV (variable, only for Local Provider)
        - Data IV (variable)
        - Encrypted Content (variable)
        """
        # Calculate lengths of each part
        efk_len = len(encrypted_file_key)
        kiv_len = len(key_iv)
        div_len = len(data_iv)

        # Build header
        header = struct.pack(
            "!4sBBHHH",
            MAGIC,
            VERSION,
            provider_type,
            efk_len,
            kiv_len,
            div_len,
        )

        # Concatenate all parts
        return header + encrypted_file_key + key_iv + data_iv + encrypted_content

    def _parse_envelope(self, ciphertext: bytes) -> Tuple[int, bytes, bytes, bytes, bytes]:
        """
        Parse Envelope.

        Returns:
            (provider_type, encrypted_file_key, key_iv, data_iv, encrypted_content)
        """
        # Fixed header size: 4(magic) + 1(version) + 1(provider) + 2(efk_len) + 2(kiv_len) + 2(div_len) = 12 bytes
        HEADER_SIZE = 12

        if len(ciphertext) < HEADER_SIZE:
            raise CorruptedCiphertextError("Envelope too short")

        # Parse header
        (
            magic,
            version,
            provider_type,
            efk_len,
            kiv_len,
            div_len,
        ) = struct.unpack("!4sBBHHH", ciphertext[:HEADER_SIZE])

        # Verify magic and version
        if magic != MAGIC:
            raise InvalidMagicError(f"Invalid magic: {magic}")
        if version != VERSION:
            raise CorruptedCiphertextError(f"Unsupported version: {version}")

        # Calculate offsets for each part
        offset = HEADER_SIZE
        efk_end = offset + efk_len
        kiv_end = efk_end + kiv_len
        div_end = kiv_end + div_len

        # Verify length
        if len(ciphertext) < div_end:
            raise CorruptedCiphertextError("Incomplete envelope")

        # Extract each part
        encrypted_file_key = ciphertext[offset:efk_end]
        key_iv = ciphertext[efk_end:kiv_end]
        data_iv = ciphertext[kiv_end:div_end]
        encrypted_content = ciphertext[div_end:]

        return provider_type, encrypted_file_key, key_iv, data_iv, encrypted_content

    async def _aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        """AES-GCM encryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key)
            return aesgcm.encrypt(iv, plaintext, associated_data=None)
        except ImportError:
            from openviking.crypto.exceptions import ConfigError

            raise ConfigError("cryptography library is required for encryption")

    async def _aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
        """AES-GCM decryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key)
            return aesgcm.decrypt(iv, ciphertext, associated_data=None)
        except ImportError:
            from openviking.crypto.exceptions import ConfigError

            raise ConfigError("cryptography library is required for encryption")
        except Exception as e:
            raise AuthenticationFailedError(f"Decryption failed: {e}")
