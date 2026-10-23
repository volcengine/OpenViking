# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for KeyProvider implementations.
"""

import asyncio
import os
import tempfile
import threading
import time

import pytest

import openviking.crypto.providers as providers_module
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


def test_concurrent_bootstrap_publishes_one_key(monkeypatch, tmp_path):
    key_file = tmp_path / "master.key"
    first_generated = threading.Event()
    second_generated = threading.Event()
    release_first = threading.Event()
    generation_guard = threading.Lock()
    generation_count = 0

    def controlled_token_bytes(length: int) -> bytes:
        nonlocal generation_count
        assert length == 32
        with generation_guard:
            generation_count += 1
            call = generation_count
        if call == 1:
            first_generated.set()
            assert release_first.wait(timeout=10)
            return b"a" * 32
        second_generated.set()
        return b"b" * 32

    monkeypatch.setattr(providers_module.secrets, "token_bytes", controlled_token_bytes)
    results = {}
    errors = []

    def load(worker: int) -> None:
        try:
            provider = LocalFileProvider(str(key_file))
            results[worker] = asyncio.run(provider.get_root_key())
        except BaseException as exc:  # pragma: no cover - reported by the parent assertion
            errors.append(exc)

    first = threading.Thread(target=load, args=(0,))
    second = threading.Thread(target=load, args=(1,))
    first.start()
    assert first_generated.wait(timeout=10)
    second.start()
    time.sleep(0.1)
    assert not second_generated.is_set()
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert generation_count == 1
    assert results == {0: b"a" * 32, 1: b"a" * 32}
    assert bytes.fromhex(key_file.read_text()) == b"a" * 32
    assert key_file.stat().st_mode & 0o777 == 0o600
