# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LocalEncryptionProviderConfig(BaseModel):
    """Local file encryption provider configuration.

    Uses a local file to store the Root Key.
    Suitable for single-user or development environments.
    """

    key_file: str = Field(
        default="~/.openviking/master.key", description="Path to local root key file"
    )


class VaultEncryptionProviderConfig(BaseModel):
    """HashiCorp Vault encryption provider configuration.

    Uses HashiCorp Vault Transit Secrets Engine for key management.
    Suitable for enterprise environments requiring centralized key management.
    """

    address: Optional[str] = Field(default=None, description="HashiCorp Vault address")
    token: Optional[str] = Field(default=None, description="HashiCorp Vault token")
    mount_point: str = Field(
        default="transit", description="HashiCorp Vault transit secrets engine mount point"
    )
    key_name: str = Field(default="openviking-root", description="HashiCorp Vault key name")


class VolcengineKMSEncryptionProviderConfig(BaseModel):
    """Volcengine KMS encryption provider configuration.

    Uses Volcengine Key Management Service for key management.
    Suitable for production environments on Volcengine.
    """

    key_id: Optional[str] = Field(default=None, description="Volcengine KMS key ID")
    region: str = Field(default="cn-beijing", description="Volcengine KMS region")
    access_key: Optional[str] = Field(default=None, description="Volcengine access key ID")
    secret_key: Optional[str] = Field(default=None, description="Volcengine secret access key")


class APIKeyHashingConfig(BaseModel):
    """API key hashing configuration.

    Controls whether API keys are hashed using Argon2id before storage.
    When disabled (default), API keys are stored in plaintext within
    AES-GCM encrypted files, allowing admin users to retrieve full keys.
    When enabled, API keys are hashed with Argon2id (one-way),
    providing maximum security but preventing key recovery.
    """

    enabled: bool = Field(
        default=False,
        description="Whether API key Argon2id hashing is enabled. "
        "Default: false - rely on file-level AES encryption for protection.",
    )


class EncryptionConfig(BaseModel):
    """Configuration for encryption module.

    Provides configuration for multi-tenant encryption functionality including:
    - Envelope encryption with AES-256-GCM
    - Multiple key providers (Local File, Vault, Volcengine KMS)
    - API Key hashing with Argon2id

    Example configurations:
        # Local file provider
        {
            "enabled": true,
            "provider": "local",
            "local": {
                "key_file": "~/.openviking/master.key"
            }
        }

        # Vault provider
        {
            "enabled": true,
            "provider": "vault",
            "vault": {
                "address": "https://vault.example.com:8200",
                "token": "vault-token-xxx",
                "mount_point": "transit",
                "key_name": "openviking-root"
            }
        }
    """

    enabled: bool = Field(default=False, description="Whether encryption is enabled")

    provider: str = Field(
        default="local",
        description="Key provider type: 'local', 'vault', 'volcengine_kms'",
    )

    local: LocalEncryptionProviderConfig = Field(
        default_factory=LocalEncryptionProviderConfig,
        description="Local provider configuration",
    )

    vault: VaultEncryptionProviderConfig = Field(
        default_factory=VaultEncryptionProviderConfig,
        description="Vault provider configuration",
    )

    volcengine_kms: VolcengineKMSEncryptionProviderConfig = Field(
        default_factory=VolcengineKMSEncryptionProviderConfig,
        description="Volcengine KMS provider configuration",
    )

    api_key_hashing: APIKeyHashingConfig = Field(
        default_factory=APIKeyHashingConfig,
        description="API key hashing configuration. "
        "Controls whether API keys are hashed using Argon2id before storage.",
    )

    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional encryption-specific parameters"
    )

    model_config = {"extra": "forbid"}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EncryptionConfig":
        """Create configuration from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            EncryptionConfig instance
        """
        return cls(**data)
