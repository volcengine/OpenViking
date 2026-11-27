# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility between the two supported local root-key sources."""

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import LocalFileProvider


async def test_inline_and_file_local_providers_are_ciphertext_compatible(tmp_path):
    root_key = bytes(range(32)).hex()
    key_file = tmp_path / "master.key"
    key_file.write_text(root_key)
    file_encryptor = FileEncryptor(LocalFileProvider(key_file=str(key_file)))
    inline_encryptor = FileEncryptor(LocalFileProvider(root_key=root_key))
    plaintext = b"compatible local provider ciphertext"

    file_ciphertext = await file_encryptor.encrypt("test_account", plaintext)
    inline_ciphertext = await inline_encryptor.encrypt("test_account", plaintext)

    assert file_ciphertext[:6] == inline_ciphertext[:6] == b"OVE1\x01\x01"
    assert await inline_encryptor.decrypt("test_account", file_ciphertext) == plaintext
    assert await file_encryptor.decrypt("test_account", inline_ciphertext) == plaintext
