# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for KeyProvider implementations.
"""

import os
import tempfile

import pytest

from openviking.crypto.encryptor import PROVIDER_LOCAL, FileEncryptor
from openviking.crypto.providers import LocalFileProvider


@pytest.fixture
async def local_file_provider():
    """Create a LocalFileProvider instance with temporary file."""
    import secrets

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        # Generate valid 32-byte key in hex format
        root_key = secrets.token_bytes(32)
        f.write(root_key.hex())
    temp_path = f.name
    # Set correct permissions (0o600)
    os.chmod(temp_path, 0o600)

    try:
        provider = LocalFileProvider(key_file=temp_path)
        yield provider
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@pytest.mark.asyncio
async def test_local_file_provider_encrypt_decrypt(local_file_provider):
    """Test LocalFileProvider encryption and decryption."""
    account_id = "test_account"
    plaintext_key = b"test_file_key"

    # Encrypt
    encrypted_key, iv = await local_file_provider.encrypt_file_key(plaintext_key, account_id)
    assert encrypted_key != plaintext_key

    # Decrypt
    decrypted = await local_file_provider.decrypt_file_key(encrypted_key, iv, account_id)
    assert decrypted == plaintext_key


@pytest.mark.asyncio
async def test_local_file_provider_different_accounts(local_file_provider):
    """Test LocalFileProvider with different accounts."""
    account1 = "account1"
    account2 = "account2"
    plaintext_key = b"test_file_key"

    # Encrypt for account1
    encrypted_key1, iv1 = await local_file_provider.encrypt_file_key(plaintext_key, account1)

    # Encrypt for account2 (should be different)
    encrypted_key2, iv2 = await local_file_provider.encrypt_file_key(plaintext_key, account2)
    assert encrypted_key1 != encrypted_key2

    # Decrypt with correct account
    decrypted1 = await local_file_provider.decrypt_file_key(encrypted_key1, iv1, account1)
    assert decrypted1 == plaintext_key

    decrypted2 = await local_file_provider.decrypt_file_key(encrypted_key2, iv2, account2)
    assert decrypted2 == plaintext_key


@pytest.mark.asyncio
async def test_local_provider_accepts_inline_root_key_with_local_envelope_type():
    root_key = bytes(range(32))

    provider = LocalFileProvider(root_key=root_key.hex())

    assert await provider.get_root_key() == root_key
    assert FileEncryptor(provider).provider_type == PROVIDER_LOCAL


@pytest.mark.asyncio
async def test_inline_and_file_local_providers_are_ciphertext_compatible(tmp_path):
    root_key = bytes(range(32))
    key_file = tmp_path / "master.key"
    key_file.write_text(root_key.hex())
    file_encryptor = FileEncryptor(LocalFileProvider(key_file=str(key_file)))
    inline_encryptor = FileEncryptor(LocalFileProvider(root_key=root_key.hex()))
    account_id = "test_account"
    plaintext = b"compatible local provider ciphertext"

    file_ciphertext = await file_encryptor.encrypt(account_id, plaintext)
    inline_ciphertext = await inline_encryptor.encrypt(account_id, plaintext)

    assert await inline_encryptor.decrypt(account_id, file_ciphertext) == plaintext
    assert await file_encryptor.decrypt(account_id, inline_ciphertext) == plaintext
