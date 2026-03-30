# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Encryption module configuration management.

Provides configuration validation and encryption module initialization.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import ConfigError
from openviking.crypto.providers import create_root_key_provider
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def validate_encryption_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate encryption configuration.

    Args:
        config: Configuration dictionary

    Returns:
        (is_valid, errors)
    """
    errors = []
    encryption_config = config.get("encryption", {})

    # Check enabled
    enabled = encryption_config.get("enabled", False)
    if not isinstance(enabled, bool):
        errors.append("encryption.enabled must be a boolean")

    if not enabled:
        return len(errors) == 0, errors

    # Check provider
    provider = encryption_config.get("provider", "local")
    supported_providers = ["local", "vault", "volcengine_kms"]
    if provider not in supported_providers:
        errors.append(f"Unsupported provider: {provider}")

    # Provider-specific validation
    if provider == "local":
        errors.extend(_validate_local_provider_config(encryption_config))
    elif provider == "vault":
        errors.extend(_validate_vault_provider_config(encryption_config))
    elif provider == "volcengine_kms":
        errors.extend(_validate_volcengine_provider_config(encryption_config))

    return len(errors) == 0, errors


def _validate_local_provider_config(config: Dict[str, Any]) -> List[str]:
    """Validate Local Provider configuration."""
    errors = []
    local_config = config.get("local", {})
    key_file_path = local_config.get("key_file", "~/.openviking/master.key")

    if not key_file_path:
        errors.append("encryption.local.key_file is required")
        return errors

    # Check if file exists or can be created
    key_file = Path(key_file_path).expanduser()
    if key_file.exists():
        # Check permissions
        if os.name != "nt":  # Skip permission check on Windows
            if (key_file.stat().st_mode & 0o077) != 0:
                errors.append(f"Key file permissions too open: {key_file_path} (should be 0600)")
    else:
        # Check if parent directory exists or can be written to
        parent_dir = key_file.parent
        if parent_dir.exists():
            if not os.access(parent_dir, os.W_OK):
                errors.append(f"Cannot create key file at: {key_file_path}")
        else:
            # Check if we can write to the closest existing ancestor directory
            current = parent_dir
            while not current.exists() and current.parent != current:
                current = current.parent
            if not os.access(current, os.W_OK):
                errors.append(f"Cannot create parent directory for key file at: {parent_dir}")

    return errors


def _validate_vault_provider_config(config: Dict[str, Any]) -> List[str]:
    """Validate Vault Provider configuration."""
    errors = []
    vault_config = config.get("vault", {})
    address = vault_config.get("address")
    token = vault_config.get("token")

    if not address:
        errors.append("encryption.vault.address is required")
    if not token:
        errors.append("encryption.vault.token is required")

    return errors


def _validate_volcengine_provider_config(config: Dict[str, Any]) -> List[str]:
    """Validate Volcengine Provider configuration."""
    errors = []
    volc_config = config.get("volcengine_kms", {})
    region = volc_config.get("region")
    access_key = volc_config.get("access_key")
    secret_key = volc_config.get("secret_key")
    key_id = volc_config.get("key_id")

    if not region:
        errors.append("encryption.volcengine_kms.region is required")
    if not access_key:
        errors.append("encryption.volcengine_kms.access_key is required")
    if not secret_key:
        errors.append("encryption.volcengine_kms.secret_key is required")
    if not key_id:
        errors.append("encryption.volcengine_kms.key_id is required")

    return errors


async def bootstrap_encryption(config: Dict[str, Any]) -> Optional[FileEncryptor]:
    """
    Initialize encryption module.

    Args:
        config: Configuration dictionary

    Returns:
        FileEncryptor instance, or None if encryption is not enabled
    """
    encryption_config = config.get("encryption", {})
    if not encryption_config.get("enabled", False):
        logger.info("Encryption is disabled")
        return None

    # Validate configuration
    is_valid, errors = validate_encryption_config(config)
    if not is_valid:
        error_msg = "; ".join(errors)
        raise ConfigError(f"Invalid encryption configuration: {error_msg}")

    # Create Provider
    provider_type = encryption_config.get("provider", "local")
    provider = create_root_key_provider(provider_type, encryption_config)

    # Create FileEncryptor
    encryptor = FileEncryptor(provider)
    logger.info("Encryption bootstrapped successfully with provider: %s", provider_type)

    return encryptor


async def encryption_health_check(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Encryption module health check.

    Args:
        config: Configuration dictionary

    Returns:
        Health status dictionary
    """
    result = {"status": "healthy", "checks": {}}

    encryption_config = config.get("encryption", {})
    if not encryption_config.get("enabled", False):
        result["checks"]["encryption"] = "disabled"
        return result

    try:
        # 1. Configuration validation
        is_valid, errors = validate_encryption_config(config)
        if not is_valid:
            result["status"] = "unhealthy"
            result["checks"]["config"] = {"status": "failed", "errors": errors}
            return result
        result["checks"]["config"] = {"status": "passed"}

        # 2. Initialize encryption module
        encryptor = await bootstrap_encryption(config)
        if encryptor is None:
            result["status"] = "unhealthy"
            result["error"] = "Failed to bootstrap encryption"
            return result
        result["checks"]["bootstrap"] = {"status": "passed"}

        # 3. Test encrypt/decrypt
        test_account = "health-check-account"
        test_content = b"health check test"

        encrypted_content = await encryptor.encrypt(test_account, test_content)
        result["checks"]["encrypt"] = {"status": "passed"}

        decrypted_content = await encryptor.decrypt(test_account, encrypted_content)
        assert decrypted_content == test_content
        result["checks"]["decrypt"] = {"status": "passed"}

    except Exception as e:
        result["status"] = "unhealthy"
        result["error"] = str(e)
        logger.exception("Encryption health check failed")

    return result
