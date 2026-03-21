# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for FileEncryptor.
"""

import os
import secrets
import tempfile

import pytest

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import LocalFileProvider


@pytest.fixture
async def encryptor():
    """Create a FileEncryptor instance with LocalFileProvider."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        # Generate valid 32-byte key in hex format
        root_key = secrets.token_bytes(32)
        f.write(root_key.hex())
    temp_path = f.name
    # Set correct permissions (0o600)
    os.chmod(temp_path, 0o600)

    try:
        provider = LocalFileProvider(key_file=temp_path)
        yield FileEncryptor(provider)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip(encryptor):
    """Test encryption and decryption roundtrip."""
    account_id = "test_account"
    plaintext = b"Hello, OpenViking!"

    # Encrypt
    ciphertext = await encryptor.encrypt(account_id, plaintext)
    assert ciphertext != plaintext
    assert ciphertext.startswith(b"OVE1")

    # Decrypt
    decrypted = await encryptor.decrypt(account_id, ciphertext)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_decrypt_unencrypted_data(encryptor):
    """Test decrypting unencrypted data."""
    account_id = "test_account"
    plaintext = b"Unencrypted data"

    # Decrypt should return the same data
    decrypted = await encryptor.decrypt(account_id, plaintext)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_decrypt_corrupted_ciphertext(encryptor):
    """Test decrypting corrupted ciphertext."""
    account_id = "test_account"
    corrupted = b"OVE1invalid"

    from openviking.crypto.exceptions import CorruptedCiphertextError

    with pytest.raises(CorruptedCiphertextError):
        await encryptor.decrypt(account_id, corrupted)


@pytest.mark.asyncio
async def test_encrypt_empty_data(encryptor):
    """Test encrypting empty data."""
    account_id = "test_account"
    plaintext = b""

    ciphertext = await encryptor.encrypt(account_id, plaintext)
    assert ciphertext.startswith(b"OVE1")

    decrypted = await encryptor.decrypt(account_id, ciphertext)
    assert decrypted == b""


@pytest.mark.asyncio
async def test_different_accounts(encryptor):
    """Test encryption/decryption across different accounts."""
    account1 = "account1"
    account2 = "account2"
    plaintext = b"Test data"

    # Encrypt with account1
    ciphertext = await encryptor.encrypt(account1, plaintext)

    # Decrypt with account1 should work
    decrypted1 = await encryptor.decrypt(account1, ciphertext)
    assert decrypted1 == plaintext

    from openviking.crypto.exceptions import KeyMismatchError

    # Decrypt with account2 should fail
    with pytest.raises(KeyMismatchError):
        await encryptor.decrypt(account2, ciphertext)
