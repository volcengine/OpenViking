"""Standalone configuration preserves the mounted KMS root key path."""

from pathlib import Path

from openviking.crypto.providers import create_root_key_provider
from openviking_gateway.config import StandaloneGatewayConfig


def test_standalone_kms_key_file_reaches_provider():
    key_file = "/etc/openviking-gateway/keys/root-key"
    config = StandaloneGatewayConfig(
        openviking_url="https://ov.example.com",
        encryption={
            "enabled": True,
            "provider": "volcengine_kms",
            "volcengine_kms": {
                "region": "cn-beijing",
                "access_key": "test-access-key",
                "secret_key": "test-secret-key",
                "key_id": "test-key-id",
                "key_file": key_file,
            },
        },
    )
    assert config.encryption["volcengine_kms"]["key_file"] == key_file
    provider = create_root_key_provider("volcengine_kms", config.encryption)
    assert provider.key_file == Path(key_file)
