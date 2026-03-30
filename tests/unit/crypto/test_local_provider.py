# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for KeyProvider implementations.
"""

import os
import tempfile

import pytest

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
