# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for crypto configuration.
"""

import pytest

from openviking.crypto.config import bootstrap_encryption


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
async def test_bootstrap_encryption_invalid_provider():
    """Test bootstrap_encryption with invalid provider."""
    config = {"encryption": {"enabled": True, "provider": "invalid_provider"}}

    from openviking.crypto.exceptions import ConfigError

    with pytest.raises(ConfigError):
        await bootstrap_encryption(config)
