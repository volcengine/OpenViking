# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""The local root-key file must hold exactly one 32-byte root key."""

import secrets

import pytest

from openviking.crypto.exceptions import ConfigError
from openviking.crypto.providers import LocalFileProvider


async def test_short_root_key_file_is_rejected(tmp_path):
    key_file = tmp_path / "master.key"
    # 16 valid hex bytes: parseable, but not a 256-bit root key.
    key_file.write_text("ab" * 16)

    provider = LocalFileProvider(key_file=str(key_file))

    with pytest.raises(ConfigError):
        await provider.get_root_key()


async def test_long_root_key_file_is_rejected(tmp_path):
    key_file = tmp_path / "master.key"
    key_file.write_text("cd" * 64)

    provider = LocalFileProvider(key_file=str(key_file))

    with pytest.raises(ConfigError):
        await provider.get_root_key()


async def test_valid_root_key_file_still_loads(tmp_path):
    root_key = secrets.token_bytes(32)
    key_file = tmp_path / "master.key"
    key_file.write_text(root_key.hex())

    provider = LocalFileProvider(key_file=str(key_file))

    assert await provider.get_root_key() == root_key
