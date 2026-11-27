# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the persisted encryption envelope."""

import pytest

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    CorruptedCiphertextError,
    EncryptionError,
    KeyMismatchError,
)
from openviking.crypto.providers import LocalFileProvider


@pytest.fixture
def encryptor():
    return FileEncryptor(LocalFileProvider(root_key=bytes(range(32)).hex()))


async def test_encrypt_decrypt_roundtrip(encryptor):
    plaintext = b"Hello, OpenViking!"
    ciphertext = await encryptor.encrypt("account1", plaintext)

    assert ciphertext.startswith(b"OVE1\x01\x01")
    assert plaintext not in ciphertext
    assert await encryptor.decrypt("account1", ciphertext) == plaintext
    with pytest.raises(EncryptionError) as failure:
        await encryptor.decrypt("account1", ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))
    assert isinstance(failure.value, AuthenticationFailedError)


@pytest.mark.parametrize("plaintext", [b"", b"O", b"OV", b"OVE", b"Unencrypted data"])
async def test_decrypt_unencrypted_data(encryptor, plaintext):
    assert await encryptor.decrypt("account1", plaintext) == plaintext


async def test_decrypt_corrupted_ciphertext(encryptor):
    with pytest.raises(EncryptionError) as failure:
        await encryptor.decrypt("account1", b"OVE1")
    assert isinstance(failure.value, CorruptedCiphertextError)


async def test_encrypt_empty_data(encryptor):
    ciphertext = await encryptor.encrypt("account1", b"")

    assert ciphertext.startswith(b"OVE1")
    assert await encryptor.decrypt("account1", ciphertext) == b""


async def test_different_accounts(encryptor):
    ciphertext = await encryptor.encrypt("account1", b"Account 1 private data")

    with pytest.raises(EncryptionError) as failure:
        await encryptor.decrypt("account2", ciphertext)
    assert isinstance(failure.value, KeyMismatchError)
