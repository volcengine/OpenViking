# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for crypto configuration.
"""

import pytest

from openviking.crypto.config import bootstrap_encryption, validate_encryption_config
from openviking_cli.utils.config.encryption_config import (
    EncryptionConfig,
    LocalEncryptionProviderConfig,
)


@pytest.mark.asyncio
async def test_bootstrap_encryption_disabled():
    """Test bootstrap_encryption with encryption disabled."""
    config = {"encryption": {"enabled": False}}
    encryptor = await bootstrap_encryption(config)
    assert encryptor is None


@pytest.mark.asyncio
async def test_bootstrap_encryption_local_file(tmp_path):
    """Test bootstrap_encryption with local file provider."""
    # Create temporary key file with hex format and correct permissions
    import os
    import secrets

    key_file = tmp_path / "master.key"
    # Generate valid 32-byte key in hex format
    root_key = secrets.token_bytes(32)
    key_file.write_text(root_key.hex())
    # Set correct permissions (0o600)
    os.chmod(key_file, 0o600)

    config = {
        "encryption": {
            "enabled": True,
            "provider": "local",
            "local": {"key_file": str(key_file)},
        }
    }

    encryptor = await bootstrap_encryption(config)
    assert encryptor is not None


@pytest.mark.asyncio
async def test_bootstrap_encryption_inline_local_root_key(tmp_path, monkeypatch):
    """The local provider can use the existing 32-byte key directly from config."""
    monkeypatch.setenv("HOME", str(tmp_path))
    root_key = bytes(range(32))
    encryption = EncryptionConfig(
        enabled=True,
        provider="local",
        local={"root_key": root_key.hex()},
    )

    encryptor = await bootstrap_encryption({"encryption": encryption.model_dump()})

    assert encryptor is not None
    assert await encryptor.provider.get_root_key() == root_key


def test_local_root_key_and_key_file_are_mutually_exclusive(tmp_path):
    config = {
        "encryption": {
            "enabled": True,
            "provider": "local",
            "local": {
                "key_file": str(tmp_path / "master.key"),
                "root_key": "00" * 32,
            },
        }
    }

    is_valid, errors = validate_encryption_config(config)

    assert not is_valid
    assert errors == [
        "encryption.local.root_key and encryption.local.key_file are mutually exclusive"
    ]


@pytest.mark.parametrize("root_key", ["00" * 31, "zz" * 32, 123])
def test_local_root_key_must_be_32_byte_hex(root_key):
    config = {
        "encryption": {
            "enabled": True,
            "provider": "local",
            "local": {"root_key": root_key},
        }
    }

    is_valid, errors = validate_encryption_config(config)

    assert not is_valid
    assert errors == [
        "encryption.local.root_key must be a 64-character hexadecimal string"
    ]
    assert str(root_key) not in errors[0]


def test_local_encryption_config_hides_inline_root_key_from_repr():
    root_key = "ab" * 32

    config = LocalEncryptionProviderConfig(root_key=root_key)

    assert config.root_key == root_key
    assert config.key_file is None
    assert root_key not in repr(config)


def test_local_encryption_config_preserves_default_key_file():
    config = LocalEncryptionProviderConfig()

    assert config.key_file == "~/.openviking/master.key"


@pytest.mark.asyncio
async def test_bootstrap_encryption_invalid_provider():
    """Test bootstrap_encryption with invalid provider."""
    config = {"encryption": {"enabled": True, "provider": "invalid_provider"}}

    from openviking.crypto.exceptions import ConfigError

    with pytest.raises(ConfigError):
        await bootstrap_encryption(config)
