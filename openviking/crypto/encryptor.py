# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File encryptor - envelope encryption implementation.

Implements Envelope Encryption pattern:
- Each file has independent random File Key
- File Key is encrypted with Account Key
- Account Key is derived from Root Key
"""

import importlib
import secrets
import struct
import time
from typing import TYPE_CHECKING, Any, Tuple

from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    CorruptedCiphertextError,
    InvalidMagicError,
    KeyMismatchError,
)
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.crypto.providers import RootKeyProvider

logger = get_logger(__name__)

# Magic number: OpenViking Encryption v1
MAGIC = b"OVE1"
MAGIC_LENGTH = len(MAGIC)

# Envelope format version
VERSION = 0x01

PROVIDER_LOCAL = 0x01
PROVIDER_VAULT = 0x02
PROVIDER_VOLCENGINE = 0x03


def _record_encryption_metrics(
    *metrics: Tuple[str, dict[str, Any]],
    debug_message: str,
) -> None:
    """Emit encryption metrics without letting observability failures affect crypto flows."""
    try:
        encryption_module = importlib.import_module("openviking.metrics.datasources.encryption")
        datasource = encryption_module.EncryptionEventDataSource
        for metric_name, metric_kwargs in metrics:
            getattr(datasource, metric_name)(**metric_kwargs)
    except Exception:
        logger.debug(debug_message, exc_info=True)


class FileEncryptor:
    """File encryptor."""

    def __init__(self, provider: "RootKeyProvider"):
        """
        Initialize FileEncryptor.

        Args:
            provider: RootKeyProvider instance
        """
        self.provider = provider
        self._provider_type = self._detect_provider_type(provider)

    def _detect_provider_type(self, provider: "RootKeyProvider") -> int:
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
        start = time.perf_counter()
        # Metrics must be best-effort: encryption must succeed/fail solely based on crypto logic,
        # not on observability availability. Therefore all metrics emissions are wrapped in try/except.
        _record_encryption_metrics(
            ("record_payload_size", {"operation": "encrypt", "size_bytes": len(plaintext)}),
            ("record_bytes", {"operation": "encrypt", "size_bytes": len(plaintext)}),
            debug_message="Failed to record encryption pre-encrypt metrics",
        )

        status = "ok"
        try:
            file_key = secrets.token_bytes(32)
            data_iv = secrets.token_bytes(12)
            encrypted_content = await self._aes_gcm_encrypt(file_key, data_iv, plaintext)
            encrypted_file_key, key_iv = await self.provider.encrypt_file_key(file_key, account_id)
            return self._build_envelope(
                self._provider_type,
                encrypted_file_key,
                key_iv,
                data_iv,
                encrypted_content,
            )
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_operation",
                    {
                        "operation": "encrypt",
                        "status": status,
                        "duration_seconds": elapsed,
                    },
                ),
                debug_message="Failed to record encryption operation metrics",
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

        start = time.perf_counter()
        status = "ok"
        try:
            file_key = await self.provider.decrypt_file_key(encrypted_file_key, key_iv, account_id)
        except Exception as e:
            status = "error"
            raise KeyMismatchError(f"Failed to decrypt file key: {e}")

        try:
            plaintext = await self._aes_gcm_decrypt(file_key, data_iv, encrypted_content)
            _record_encryption_metrics(
                ("record_payload_size", {"operation": "decrypt", "size_bytes": len(ciphertext)}),
                ("record_bytes", {"operation": "decrypt", "size_bytes": len(ciphertext)}),
                debug_message="Failed to record encryption pre-decrypt metrics",
            )
            return plaintext
        except AuthenticationFailedError as e:
            status = "error"
            _record_encryption_metrics(
                ("record_auth_failed", {}),
                debug_message="Failed to record encryption authentication failure metrics",
            )
            raise AuthenticationFailedError(f"Authentication failed: {e}")
        except Exception as e:
            status = "error"
            raise AuthenticationFailedError(f"Authentication failed: {e}")
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_operation",
                    {
                        "operation": "decrypt",
                        "status": status,
                        "duration_seconds": elapsed,
                    },
                ),
                debug_message="Failed to record decryption operation metrics",
            )

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
