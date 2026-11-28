# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Root-key persistence with offline Vault and Volcengine SDK boundaries."""

import base64
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import ConfigError
from openviking.crypto.providers import LocalFileProvider, VaultProvider, VolcengineKMSProvider
from openviking.metrics.datasources.encryption import EncryptionEventDataSource


@pytest.fixture
def vault_client(monkeypatch):
    class InvalidPath(Exception):
        pass

    hvac = ModuleType("hvac")
    exceptions = ModuleType("hvac.exceptions")
    exceptions.__dict__["InvalidPath"] = InvalidPath
    hvac.__dict__["exceptions"] = exceptions
    monkeypatch.setitem(sys.modules, "hvac", hvac)
    monkeypatch.setitem(sys.modules, "hvac.exceptions", exceptions)

    client = Mock()
    monkeypatch.setattr(VaultProvider, "_get_client", AsyncMock(return_value=client))
    return client, InvalidPath


@pytest.fixture
def volcengine_client(monkeypatch, tmp_path):
    client = Mock()
    monkeypatch.setattr(VolcengineKMSProvider, "_get_kms_client", AsyncMock(return_value=client))
    monkeypatch.setattr("openviking.crypto.providers.Path.home", lambda: tmp_path)
    return client


class TestVaultProviderMock:
    async def test_root_key_read_failure_never_creates(self, vault_client):
        client, _ = vault_client
        client.secrets.kv.v1.read_secret.side_effect = RuntimeError("Vault unavailable")
        provider = VaultProvider(addr="http://vault.test", token="test-token")

        with pytest.raises(RuntimeError, match="Vault unavailable"):
            await provider.get_root_key()

        client.secrets.kv.v1.create_or_update_secret.assert_not_called()

    async def test_persist_failure_clears_cache_and_retries(self, vault_client):
        client, invalid_path = vault_client
        provider = VaultProvider(addr="http://vault.test", token="test-token")
        kv = client.secrets.kv.v1
        kv.read_secret.side_effect = invalid_path()
        kv.create_or_update_secret.side_effect = RuntimeError("write outage")
        transit = client.secrets.transit
        transit.encrypt_data.return_value = {"data": {"ciphertext": "vault:v1:wrapped-key"}}

        with pytest.raises(ConfigError, match="data loss risk"):
            await provider.get_root_key()

        kv.create_or_update_secret.side_effect = None
        encryptor = FileEncryptor(provider)
        ciphertext = await encryptor.encrypt("account", b"persisted data")
        assert ciphertext.startswith(b"OVE1\x01\x02")

        persisted = kv.create_or_update_secret.call_args.kwargs
        assert kv.create_or_update_secret.call_count == 2
        assert persisted == {
            "path": "openviking-encrypted-root-key",
            "mount_point": "secret",
            "secret": {"encrypted_root_key": base64.b64encode(b"vault:v1:wrapped-key").decode()},
        }
        generated_key = transit.encrypt_data.call_args.kwargs["plaintext"]
        assert len(base64.b64decode(generated_key)) == 32
        kv.read_secret.side_effect = None
        kv.read_secret.return_value = {"data": persisted["secret"]}
        transit.decrypt_data.return_value = {"data": {"plaintext": generated_key}}

        reopened = FileEncryptor(VaultProvider(addr="http://vault.test", token="test-token"))
        assert await reopened.decrypt("account", ciphertext) == b"persisted data"
        transit.decrypt_data.assert_called_once_with(
            name="openviking-root-key", ciphertext="vault:v1:wrapped-key", mount_point="transit"
        )

    async def test_root_key_decrypt_failure_never_creates(self, vault_client):
        client, _ = vault_client
        client.secrets.kv.v1.read_secret.return_value = {
            "data": {"encrypted_root_key": base64.b64encode(b"vault:v1:wrapped-key").decode()}
        }
        client.secrets.transit.decrypt_data.side_effect = RuntimeError("bad key")
        provider = VaultProvider(addr="http://vault.test", token="test-token")

        with pytest.raises(RuntimeError, match="bad key"):
            await provider.get_root_key()

        client.secrets.kv.v1.create_or_update_secret.assert_not_called()


class TestVolcengineKMSProviderMock:
    async def test_persist_failure_clears_cache_and_retries(self, volcengine_client, tmp_path):
        key_file = tmp_path / "root-key.enc"
        config = {
            "region": "cn-beijing",
            "access_key_id": "test-access-key",
            "secret_access_key": "test-secret-key",
            "key_id": "test-key-id",
            "key_file": str(key_file),
        }
        encryptor = FileEncryptor(VolcengineKMSProvider(**config))
        volcengine_client.encrypt.side_effect = RuntimeError("KMS down")

        with pytest.raises(ConfigError, match="data loss risk"):
            await encryptor.encrypt("account", b"persisted data")

        assert not key_file.exists()
        volcengine_client.encrypt.side_effect = None
        volcengine_client.encrypt.return_value = b"kms-wrapped-key"
        ciphertext = await encryptor.encrypt("account", b"persisted data")

        assert ciphertext.startswith(b"OVE1\x01\x03")
        assert key_file.read_bytes() == b"kms-wrapped-key"
        key_id, generated_key = volcengine_client.encrypt.call_args.args
        assert key_id == "test-key-id"
        assert len(generated_key) == 32
        volcengine_client.decrypt.return_value = generated_key

        reopened = FileEncryptor(VolcengineKMSProvider(**config))
        assert await reopened.decrypt("account", ciphertext) == b"persisted data"
        volcengine_client.decrypt.assert_called_once_with(b"kms-wrapped-key", "test-key-id")

    async def test_missing_key_file_never_shadows_the_default_root_key(
        self, volcengine_client, tmp_path
    ):
        default_key_file = tmp_path / ".openviking" / VolcengineKMSProvider.ROOT_KEY_FILENAME
        default_key_file.parent.mkdir()
        default_key_file.write_bytes(b"existing-root-key")
        configured_key_file = tmp_path / "configured" / "root-key.enc"
        provider = VolcengineKMSProvider(
            region="cn-beijing",
            access_key_id="test-access-key",
            secret_access_key="test-secret-key",
            key_id="test-key-id",
            key_file=str(configured_key_file),
        )

        with pytest.raises(ConfigError, match="move it to"):
            await provider.get_root_key()

        volcengine_client.encrypt.assert_not_called()
        assert not configured_key_file.exists()
        assert default_key_file.read_bytes() == b"existing-root-key"


async def test_get_root_key_survives_metrics_failure(tmp_path, monkeypatch):
    key_file = tmp_path / "root.key"
    root_key = b"k" * 32
    key_file.write_text(root_key.hex())
    provider = LocalFileProvider(key_file=str(key_file))

    failed_metric = Mock(side_effect=RuntimeError("metrics write failed"))
    monkeypatch.setattr(EncryptionEventDataSource, "record_key_load", failed_metric)

    assert await provider.get_root_key() == root_key
    failed_metric.assert_called_once()
