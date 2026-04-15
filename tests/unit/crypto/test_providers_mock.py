# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Mock-based unit tests for all KeyProvider implementations.

These tests do NOT require external services (Vault, Volcengine KMS) to run.
They use mocking to verify:
1. Envelope format compatibility
2. Provider-type byte round-tripping
3. API interaction correctness
4. Error handling
"""

import base64
import os
import secrets
import struct
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import pytest

import openviking.crypto.providers as providers_module
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import (
    PROVIDER_LOCAL,
    PROVIDER_VAULT,
    PROVIDER_VOLCENGINE,
    LocalFileProvider,
    VaultProvider,
    VolcengineKMSProvider,
)
from openviking.metrics.datasources.encryption import EncryptionEventDataSource


@pytest.fixture
def local_file_provider():
    """Create a LocalFileProvider instance with temporary file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        root_key = secrets.token_bytes(32)
        f.write(root_key.hex())
    temp_path = f.name
    os.chmod(temp_path, 0o600)

    try:
        provider = LocalFileProvider(key_file=temp_path)
        yield provider
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@pytest.fixture
def vault_mock_provider():
    """Create a VaultProvider with mocked methods."""
    provider = VaultProvider(
        addr="http://mock-vault:8200", token="mock-token", mount_path="transit"
    )

    # Mock all the methods that would call external services
    provider._get_client = AsyncMock()
    provider._ensure_transit_engine_enabled = AsyncMock()
    provider._ensure_root_key_exists = AsyncMock()

    yield provider


@pytest.fixture
def volcengine_mock_provider():
    """Create a VolcengineKMSProvider with mocked methods."""
    provider = VolcengineKMSProvider(
        access_key_id="mock-access-key",
        secret_access_key="mock-secret-key",
        region="cn-beijing",
        key_id="mock-key-id",
    )

    # Mock all the methods that would call external services
    provider._get_kms_client = AsyncMock()

    yield provider


class TestEnvelopeFormat:
    """Tests for envelope format and provider-type byte round-tripping."""

    @pytest.mark.asyncio
    async def test_local_provider_envelope_format(self, local_file_provider):
        """Test envelope format with LocalFileProvider."""
        encryptor = FileEncryptor(local_file_provider)
        account_id = "test-account"
        plaintext = b"This is a test message"

        # Encrypt
        ciphertext = await encryptor.encrypt(account_id, plaintext)

        # Verify envelope starts with magic
        assert ciphertext.startswith(b"OVE1")

        # Verify provider type is in envelope
        assert ciphertext[5] == PROVIDER_LOCAL

        # Decrypt back
        decrypted = await encryptor.decrypt(account_id, ciphertext)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_vault_provider_type_detection(self):
        """Test that VaultProvider type is correctly detected by FileEncryptor."""
        # Create a minimal mock provider that just needs type detection
        mock_provider = Mock(spec=VaultProvider)

        # FileEncryptor should detect it as Vault type
        encryptor = FileEncryptor(mock_provider)
        assert encryptor._provider_type == PROVIDER_VAULT

    @pytest.mark.asyncio
    async def test_volcengine_provider_type_detection(self):
        """Test that VolcengineKMSProvider type is correctly detected by FileEncryptor."""
        # Create a minimal mock provider that just needs type detection
        mock_provider = Mock(spec=VolcengineKMSProvider)

        # FileEncryptor should detect it as Volcengine type
        encryptor = FileEncryptor(mock_provider)
        assert encryptor._provider_type == PROVIDER_VOLCENGINE

    @pytest.mark.asyncio
    async def test_provider_type_roundtrip(self, local_file_provider):
        """Test that provider type is correctly preserved in envelope."""
        encryptor = FileEncryptor(local_file_provider)
        account_id = "test-account"
        plaintext = b"Roundtrip test"

        ciphertext = await encryptor.encrypt(account_id, plaintext)

        # Parse the envelope manually to verify provider type
        HEADER_SIZE = 12
        assert len(ciphertext) >= HEADER_SIZE

        (magic, version, provider_type, _, _, _) = struct.unpack(
            "!4sBBHHH", ciphertext[:HEADER_SIZE]
        )

        assert magic == b"OVE1"
        assert version == 0x01
        assert provider_type == PROVIDER_LOCAL


class TestVaultProviderMock:
    """Tests for VaultProvider with mocked methods."""

    @pytest.mark.asyncio
    async def test_vault_encrypt_decrypt_with_mock(self, vault_mock_provider):
        """Test encrypt_file_key and decrypt_file_key can be mocked."""
        account_id = "test-account"
        plaintext_key = secrets.token_bytes(32)
        mock_ciphertext = b"vault:v1:encrypted_data"
        mock_iv = secrets.token_bytes(12)

        # Setup mock methods
        vault_mock_provider.encrypt_file_key = AsyncMock(return_value=(mock_ciphertext, mock_iv))
        vault_mock_provider.decrypt_file_key = AsyncMock(return_value=plaintext_key)

        # Encrypt
        encrypted, iv = await vault_mock_provider.encrypt_file_key(plaintext_key, account_id)
        assert encrypted == mock_ciphertext
        assert iv == mock_iv
        vault_mock_provider.encrypt_file_key.assert_called_once_with(plaintext_key, account_id)

        # Decrypt
        decrypted = await vault_mock_provider.decrypt_file_key(encrypted, iv, account_id)
        assert decrypted == plaintext_key
        vault_mock_provider.decrypt_file_key.assert_called_once_with(encrypted, iv, account_id)

    @pytest.mark.asyncio
    async def test_vault_provider_with_file_encryptor(self, vault_mock_provider):
        """Test FileEncryptor works with mocked VaultProvider."""
        # Setup mock methods
        test_file_key = secrets.token_bytes(32)
        test_iv = secrets.token_bytes(12)
        vault_mock_provider.get_root_key = AsyncMock(return_value=b"test_root_key")
        vault_mock_provider.derive_account_key = AsyncMock(return_value=test_file_key)
        vault_mock_provider.encrypt_file_key = AsyncMock(return_value=(b"encrypted_fk", test_iv))
        vault_mock_provider.decrypt_file_key = AsyncMock(return_value=test_file_key)

        encryptor = FileEncryptor(vault_mock_provider)
        account_id = "test-account"
        plaintext = b"Test with Vault mock"

        # Patch the AES encryption to simplify
        with (
            patch.object(
                encryptor, "_aes_gcm_encrypt", new=AsyncMock(return_value=b"encrypted_data")
            ),
            patch.object(encryptor, "_aes_gcm_decrypt", new=AsyncMock(return_value=plaintext)),
        ):
            ciphertext = await encryptor.encrypt(account_id, plaintext)
            assert ciphertext.startswith(b"OVE1")
            assert ciphertext[5] == PROVIDER_VAULT


class TestVolcengineKMSProviderMock:
    """Tests for VolcengineKMSProvider with mocked methods."""

    @pytest.mark.asyncio
    async def test_volcengine_encrypt_decrypt_with_mock(self, volcengine_mock_provider):
        """Test encrypt_file_key and decrypt_file_key can be mocked."""
        account_id = "test-account"
        plaintext_key = secrets.token_bytes(32)
        mock_ciphertext = base64.b64encode(b"encrypted_data")
        mock_iv = secrets.token_bytes(12)

        # Setup mock methods
        volcengine_mock_provider.encrypt_file_key = AsyncMock(
            return_value=(mock_ciphertext, mock_iv)
        )
        volcengine_mock_provider.decrypt_file_key = AsyncMock(return_value=plaintext_key)

        # Encrypt
        encrypted, iv = await volcengine_mock_provider.encrypt_file_key(plaintext_key, account_id)
        assert encrypted == mock_ciphertext
        assert iv == mock_iv
        volcengine_mock_provider.encrypt_file_key.assert_called_once_with(plaintext_key, account_id)

        # Decrypt
        decrypted = await volcengine_mock_provider.decrypt_file_key(encrypted, iv, account_id)
        assert decrypted == plaintext_key
        volcengine_mock_provider.decrypt_file_key.assert_called_once_with(encrypted, iv, account_id)


class TestCrossProviderEnvelope:
    """Tests for cross-provider envelope behavior."""

    @pytest.mark.asyncio
    async def test_different_provider_type_in_envelope(self):
        """Test that different providers set different type bytes in envelope."""
        # Create mock providers
        mock_local = Mock(spec=LocalFileProvider)
        mock_vault = Mock(spec=VaultProvider)
        mock_volcengine = Mock(spec=VolcengineKMSProvider)

        # Each should be detected as different types
        assert FileEncryptor(mock_local)._provider_type == PROVIDER_LOCAL
        assert FileEncryptor(mock_vault)._provider_type == PROVIDER_VAULT
        assert FileEncryptor(mock_volcengine)._provider_type == PROVIDER_VOLCENGINE

    @pytest.mark.asyncio
    async def test_same_provider_different_instance_can_decrypt(self, local_file_provider):
        """Test that same provider type (different instances) can decrypt each other's data."""
        # Create second local provider with the same key file
        # First, get the key from the first provider
        root_key = await local_file_provider.get_root_key()

        # Create second provider with same key
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(root_key.hex())
        temp_path2 = f.name
        os.chmod(temp_path2, 0o600)

        try:
            provider2 = LocalFileProvider(key_file=temp_path2)

            encryptor1 = FileEncryptor(local_file_provider)
            encryptor2 = FileEncryptor(provider2)

            account_id = "test-account"
            plaintext = b"Same provider, different instance"

            # Encrypt with provider1
            ciphertext = await encryptor1.encrypt(account_id, plaintext)

            # Decrypt with provider2
            decrypted = await encryptor2.decrypt(account_id, ciphertext)
            assert decrypted == plaintext
        finally:
            if os.path.exists(temp_path2):
                os.unlink(temp_path2)


@pytest.mark.asyncio
async def test_hkdf_derive_logs_debug_when_metrics_recording_fails(
    local_file_provider, monkeypatch: pytest.MonkeyPatch
):
    """HKDF derivation should log metrics side-effect failures without changing the result."""

    debug = Mock()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("metrics write failed")

    monkeypatch.setattr(providers_module.logger, "debug", debug)
    monkeypatch.setattr(EncryptionEventDataSource, "record_key_derivation", staticmethod(_boom))

    result = await local_file_provider._hkdf_derive(
        b"a" * 32,
        "acct-1",
        b"salt",
        b"info:",
    )

    assert isinstance(result, bytes)
    assert len(result) == 32
    debug.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_factory", "provider_name"),
    [
        ("local_file_provider", "local"),
        ("vault_mock_provider", "vault"),
        ("volcengine_mock_provider", "volcengine_kms"),
    ],
)
async def test_get_root_key_logs_debug_when_metrics_recording_fails(
    request: pytest.FixtureRequest,
    provider_factory: str,
    provider_name: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Provider get_root_key should log metrics side-effect failures and still return the key."""

    provider = request.getfixturevalue(provider_factory)
    provider._root_key = None
    debug = Mock()

    if provider_name == "local":
        provider._load_or_create_root_key = AsyncMock(return_value=b"k" * 32)
    else:
        provider._get_or_create_root_key = AsyncMock(return_value=b"k" * 32)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("metrics write failed")

    monkeypatch.setattr(providers_module.logger, "debug", debug)
    monkeypatch.setattr(EncryptionEventDataSource, "record_key_load", staticmethod(_boom))

    result = await provider.get_root_key()

    assert result == b"k" * 32
    if provider_name == "local":
        provider._load_or_create_root_key.assert_awaited_once()
    else:
        provider._get_or_create_root_key.assert_awaited_once()
    debug.assert_called_once()
    (message,) = debug.call_args.args[:1]
    assert "Failed to record encryption key metrics" in str(message)
