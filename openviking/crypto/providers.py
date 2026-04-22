# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Key provider abstractions and implementations.

Provides multiple key management methods:
- LocalFileProvider: Local file storage for Root Key
- VaultProvider: HashiCorp Vault
- VolcengineKMSProvider: Volcengine KMS
"""

import abc
import asyncio
import importlib
import os
import secrets
import time
from abc import ABC
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    ConfigError,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _record_encryption_metrics(
    *metrics: Tuple[str, Dict[str, Any]],
    debug_message: Optional[str] = None,
) -> None:
    """Emit encryption metrics without creating a hard import edge back into crypto."""
    try:
        encryption_module = importlib.import_module("openviking.metrics.datasources.encryption")
        datasource = encryption_module.EncryptionEventDataSource
        for metric_name, metric_kwargs in metrics:
            getattr(datasource, metric_name)(**metric_kwargs)
    except Exception:
        if debug_message:
            logger.debug(debug_message, exc_info=True)


# HKDF related constants
HKDF_SALT = b"openviking-kek-salt-v1"
HKDF_INFO_PREFIX = b"openviking:kek:v1:"

# Provider types
PROVIDER_LOCAL = 0x01
PROVIDER_VAULT = 0x02
PROVIDER_VOLCENGINE = 0x03


class RootKeyProvider(ABC):
    """Root Key Provider abstract base class."""

    @abc.abstractmethod
    async def get_root_key(self) -> bytes:
        """Get Root Key (only used by Local Provider)."""
        pass

    @abc.abstractmethod
    async def derive_account_key(self, account_id: str) -> bytes:
        """Derive Account Key for the specified account."""
        pass

    @abc.abstractmethod
    async def encrypt_file_key(self, plaintext_key: bytes, account_id: str) -> Tuple[bytes, bytes]:
        """Encrypt File Key."""
        pass

    @abc.abstractmethod
    async def decrypt_file_key(self, encrypted_key: bytes, iv: bytes, account_id: str) -> bytes:
        """Decrypt File Key."""
        pass

    async def _hkdf_derive(
        self, root_key: bytes, account_id: str, salt: bytes, info_prefix: bytes
    ) -> bytes:
        """
        Derive key using HKDF.

        Args:
            root_key: Root key
            account_id: Account ID
            salt: HKDF salt
            info_prefix: HKDF info prefix

        Returns:
            Derived key
        """
        start = time.perf_counter()
        status = "ok"
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF

            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                info=info_prefix + account_id.encode(),
            )
            return hkdf.derive(root_key)
        except ImportError:
            status = "error"
            raise ConfigError("cryptography library is required for encryption")
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_key_derivation",
                    {"status": status, "duration_seconds": elapsed},
                ),
                debug_message="Failed to record encryption key derivation metrics",
            )


class BaseProvider(RootKeyProvider):
    """Base provider with common encryption functionality."""

    async def _aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        """AES-GCM encryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key)
            return aesgcm.encrypt(iv, plaintext, associated_data=None)
        except ImportError:
            raise ConfigError("cryptography library is required for encryption")

    async def _aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
        """AES-GCM decryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key)
            return aesgcm.decrypt(iv, ciphertext, associated_data=None)
        except ImportError:
            raise ConfigError("cryptography library is required for encryption")
        except Exception as e:
            raise AuthenticationFailedError(f"Decryption failed: {e}")

    async def encrypt_file_key(self, plaintext_key: bytes, account_id: str) -> Tuple[bytes, bytes]:
        """
        Encrypt File Key with Account Key.

        Args:
            plaintext_key: Plaintext File Key
            account_id: Account ID

        Returns:
            (encrypted_key, iv)
        """
        account_key = await self.derive_account_key(account_id)
        iv = secrets.token_bytes(12)
        encrypted_key = await self._aes_gcm_encrypt(account_key, iv, plaintext_key)
        return encrypted_key, iv

    async def decrypt_file_key(self, encrypted_key: bytes, iv: bytes, account_id: str) -> bytes:
        """
        Decrypt File Key with Account Key.

        Args:
            encrypted_key: Encrypted File Key
            iv: Initialization vector
            account_id: Account ID

        Returns:
            Decrypted File Key
        """
        account_key = await self.derive_account_key(account_id)
        return await self._aes_gcm_decrypt(account_key, iv, encrypted_key)


class LocalFileProvider(BaseProvider):
    """Local file Root Key Provider."""

    def __init__(self, key_file: str):
        """
        Initialize LocalFileProvider.

        Args:
            key_file: Root Key file path
        """
        self.key_file = Path(key_file).expanduser()
        self._root_key: Optional[bytes] = None

    async def get_root_key(self) -> bytes:
        """Get Root Key."""
        if self._root_key is not None:
            _record_encryption_metrics(("record_key_cache_hit", {"provider": "local"}))
            return self._root_key

        _record_encryption_metrics(("record_key_cache_miss", {"provider": "local"}))

        start = time.perf_counter()
        status = "ok"
        try:
            self._root_key = await self._load_or_create_root_key()
            return self._root_key
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_key_load",
                    {
                        "status": status,
                        "provider": "local",
                        "duration_seconds": elapsed,
                    },
                ),
                ("record_key_version_usage", {"key_version": "local"}),
                debug_message="Failed to record encryption key metrics for provider=local",
            )

    async def _load_or_create_root_key(self) -> bytes:
        """Load or create Root Key."""
        if self.key_file.exists():
            # Read existing key
            with open(self.key_file, "r") as f:
                hex_key = f.read().strip()
            try:
                return bytes.fromhex(hex_key)
            except ValueError:
                raise ConfigError(f"Invalid root key format in {self.key_file}")
        else:
            # Create new key
            root_key = secrets.token_bytes(32)
            # Ensure parent directory exists
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            # Write file with permissions 0600
            with open(self.key_file, "w") as f:
                f.write(root_key.hex())
            # Set file permissions
            os.chmod(self.key_file, 0o600)
            logger.info("Created new root key at %s", self.key_file)
            return root_key

    async def derive_account_key(self, account_id: str) -> bytes:
        """Derive Account Key from Root Key."""
        root_key = await self.get_root_key()
        return await self._hkdf_derive(root_key, account_id, HKDF_SALT, HKDF_INFO_PREFIX)


class VaultProvider(BaseProvider):
    """HashiCorp Vault Key Provider.

    Uses HashiCorp Vault's transit secrets engine for key management.
    Core features:
    - Root key management: Vault transit secrets engine (encrypted)
    - Account Key derivation: HKDF-SHA256 (from root key)
    - File Key encryption: AES-256-GCM (with Account Key)
    """

    def __init__(
        self,
        addr: str,
        token: str,
        mount_path: str = "transit",
        kv_mount_path: str = "secret",
        kv_version: int = 1,
        root_key_name: str = "openviking-root-key",
        encrypted_root_key_key: str = "openviking-encrypted-root-key",
    ):
        """
        Initialize VaultProvider.

        Args:
            addr: Vault server address (e.g., "http://127.0.0.1:8200")
            token: Vault authentication token
            mount_path: Transit secrets engine mount path (default: "transit")
            kv_mount_path: KV secrets engine mount path (default: "secret")
            kv_version: KV secrets engine version (1 or 2, default: 1)
            root_key_name: Transit engine key name (default: "openviking-root-key")
            encrypted_root_key_key: KV engine key path (default: "openviking-encrypted-root-key")
        """
        self.addr = addr
        self.token = token
        self.mount_path = mount_path
        self.kv_mount_path = kv_mount_path
        self.kv_version = kv_version
        self.root_key_name = root_key_name
        self.encrypted_root_key_key = encrypted_root_key_key
        self._client: Any = None
        self._root_key: Optional[bytes] = None

    async def _get_client(self):
        """
        Get or create Vault client.

        Returns:
            Vault client instance
        """
        if not self._client:
            try:
                import hvac
            except ImportError:
                raise ConfigError(
                    "hvac library is required for Vault provider. Install with: pip install hvac"
                )

            self._client = hvac.Client(url=self.addr, token=self.token)

            # Verify Vault is accessible
            is_auth = await asyncio.to_thread(self._client.is_authenticated)
            if not is_auth:
                raise AuthenticationFailedError("Failed to authenticate with Vault")

            # Ensure transit engine is enabled
            await self._ensure_transit_engine_enabled()

            # Ensure root key exists
            await self._ensure_root_key_exists()

        return self._client

    async def _ensure_transit_engine_enabled(self):
        """Ensure transit secrets engine is enabled."""
        try:
            # Check if transit engine is already enabled
            engines = await asyncio.to_thread(self._client.sys.list_mounted_secrets_engines)
            if f"{self.mount_path}/" not in engines["data"]:
                await asyncio.to_thread(
                    self._client.sys.enable_secrets_engine,
                    backend_type="transit",
                    path=self.mount_path,
                )
                logger.info(f"Enabled transit secrets engine at {self.mount_path}")
        except Exception as e:
            logger.warning(f"Failed to check/enable transit engine: {e}")

    async def _ensure_root_key_exists(self):
        """Ensure root key exists in Vault transit engine."""
        try:
            # Try to read the key
            await asyncio.to_thread(
                self._client.secrets.transit.read_key,
                name=self.root_key_name,
                mount_point=self.mount_path,
            )
        except Exception:
            # Key doesn't exist, create it
            await asyncio.to_thread(
                self._client.secrets.transit.create_key,
                name=self.root_key_name,
                key_type="aes256-gcm96",
                mount_point=self.mount_path,
            )
            logger.info(f"Created root key {self.root_key_name} in Vault")

    async def _encrypt_with_vault(self, plaintext: bytes) -> bytes:
        """
        Encrypt data with Vault transit.

        Args:
            plaintext: Plaintext data

        Returns:
            Encrypted data
        """
        client = await self._get_client()
        import base64

        plaintext_b64 = base64.b64encode(plaintext).decode("utf-8")
        response = await asyncio.to_thread(
            client.secrets.transit.encrypt_data,
            name=self.root_key_name,
            plaintext=plaintext_b64,
            mount_point=self.mount_path,
        )
        ciphertext_str = response["data"]["ciphertext"]
        return ciphertext_str.encode("utf-8")

    async def _decrypt_with_vault(self, ciphertext: bytes) -> bytes:
        """
        Decrypt data with Vault transit.

        Args:
            ciphertext: Encrypted data

        Returns:
            Decrypted data
        """
        client = await self._get_client()
        import base64

        ciphertext_str = ciphertext.decode("utf-8")
        response = await asyncio.to_thread(
            client.secrets.transit.decrypt_data,
            name=self.root_key_name,
            ciphertext=ciphertext_str,
            mount_point=self.mount_path,
        )
        return base64.b64decode(response["data"]["plaintext"])

    async def _get_or_create_root_key(self) -> bytes:
        """
        Get or create root key.

        Returns:
            Root key bytes
        """
        if self._root_key is not None:
            return self._root_key

        client = await self._get_client()
        import base64

        try:
            # Try to read encrypted root key from Vault kv
            if self.kv_version == 2:
                response = await asyncio.to_thread(
                    client.secrets.kv.v2.read_secret_version,
                    path=self.encrypted_root_key_key,
                    mount_point=self.kv_mount_path,
                )
                encrypted_root_key_b64 = response["data"]["data"]["encrypted_root_key"]
            else:
                response = await asyncio.to_thread(
                    client.secrets.kv.v1.read_secret,
                    path=self.encrypted_root_key_key,
                    mount_point=self.kv_mount_path,
                )
                encrypted_root_key_b64 = response["data"]["encrypted_root_key"]

            encrypted_root_key = base64.b64decode(encrypted_root_key_b64)
            self._root_key = await self._decrypt_with_vault(encrypted_root_key)
            logger.info("Loaded existing root key from Vault")
        except Exception:
            # Generate new root key
            import secrets

            self._root_key = secrets.token_bytes(32)

            # Encrypt and store root key in Vault kv
            encrypted_root_key = await self._encrypt_with_vault(self._root_key)
            try:
                if self.kv_version == 2:
                    await asyncio.to_thread(
                        client.secrets.kv.v2.create_or_update_secret,
                        path=self.encrypted_root_key_key,
                        mount_point=self.kv_mount_path,
                        secret={
                            "encrypted_root_key": base64.b64encode(encrypted_root_key).decode(
                                "utf-8"
                            )
                        },
                    )
                else:
                    await asyncio.to_thread(
                        client.secrets.kv.v1.create_or_update_secret,
                        path=self.encrypted_root_key_key,
                        mount_point=self.kv_mount_path,
                        secret={
                            "encrypted_root_key": base64.b64encode(encrypted_root_key).decode(
                                "utf-8"
                            )
                        },
                    )
                logger.info("Created and stored new root key in Vault")
            except Exception as e:
                raise ConfigError(
                    f"Failed to persist root key to Vault. "
                    f"Refusing to start with ephemeral key (data loss risk): {e}"
                )

        return self._root_key

    async def get_root_key(self) -> bytes:
        """
        Get root key.

        Returns:
            Root key
        """
        if self._root_key is not None:
            _record_encryption_metrics(("record_key_cache_hit", {"provider": "vault"}))
            return self._root_key

        _record_encryption_metrics(("record_key_cache_miss", {"provider": "vault"}))

        start = time.perf_counter()
        status = "ok"
        try:
            self._root_key = await self._get_or_create_root_key()
            return self._root_key
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_key_load",
                    {
                        "status": status,
                        "provider": "vault",
                        "duration_seconds": elapsed,
                    },
                ),
                ("record_key_version_usage", {"key_version": str(self.root_key_name)}),
                debug_message="Failed to record encryption key metrics for provider=vault",
            )

    async def derive_account_key(self, account_id: str) -> bytes:
        """
        Derive Account Key using HKDF.

        Args:
            account_id: Account ID

        Returns:
            Derived Account Key
        """
        root_key = await self.get_root_key()
        return await self._hkdf_derive(root_key, account_id, HKDF_SALT, HKDF_INFO_PREFIX)


class VolcengineKMSProvider(BaseProvider):
    """Volcengine KMS Key Provider.

    Suitable for production environments, using Volcengine KMS service for key management.
    Core features:
    - Root key storage: Volcengine KMS (encrypted)
    - Account Key derivation: HKDF-SHA256
    - File Key encryption: AES-256-GCM (with Account Key)
    """

    ROOT_KEY_FILENAME = "openviking-volcengine-root-key.enc"

    def __init__(
        self,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        key_id: str,
        endpoint: Optional[str] = None,
        key_file: Optional[str] = None,
    ):
        """
        Initialize Volcengine KMS Provider.

        Args:
            region: Region (e.g., cn-beijing)
            access_key_id: Volcengine Access Key ID
            secret_access_key: Volcengine Secret Access Key
            key_id: Volcengine KMS Key ID (immutable system-generated identifier)
            endpoint: Custom KMS service endpoint (optional)
            key_file: Path to encrypted root key file (default: ~/.openviking/openviking-volcengine-root-key.enc)
        """
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.key_id = key_id
        self.endpoint = endpoint or f"kms.{region}.volcengineapi.com"
        if key_file:
            self.key_file = Path(key_file).expanduser()
        else:
            self.key_file = Path.home() / ".openviking" / self.ROOT_KEY_FILENAME
        self._kms_client: Any = None
        self._root_key: Optional[bytes] = None

    async def _get_kms_client(self):
        """
        Get Volcengine KMS client.

        Returns:
            KMS client instance
        """
        if not self._kms_client:
            try:
                import base64
                import json

                from volcengine.ApiInfo import ApiInfo
                from volcengine.base.Service import Service
                from volcengine.Credentials import Credentials
                from volcengine.ServiceInfo import ServiceInfo
            except ImportError:
                raise ConfigError(
                    "volcengine is required for Volcengine KMS. "
                    "Install with: pip install volcengine"
                )

            class KmsService(Service):
                def __init__(self, region, access_key_id, secret_access_key, endpoint):
                    self.service_info = self.get_service_info(
                        region, access_key_id, secret_access_key, endpoint
                    )
                    self.api_info = self.get_api_info()
                    super(KmsService, self).__init__(self.service_info, self.api_info)

                @staticmethod
                def get_service_info(region, access_key_id, secret_access_key, endpoint):
                    credentials = Credentials(access_key_id, secret_access_key, "kms", region)
                    service_info = ServiceInfo(
                        endpoint, {"Accept": "application/json"}, credentials, 30, 30, "https"
                    )
                    return service_info

                @staticmethod
                def get_api_info():
                    api_info = {
                        "Encrypt": ApiInfo(
                            "POST", "/", {"Action": "Encrypt", "Version": "2021-02-18"}, {}, {}
                        ),
                        "Decrypt": ApiInfo(
                            "POST", "/", {"Action": "Decrypt", "Version": "2021-02-18"}, {}, {}
                        ),
                    }
                    return api_info

                def encrypt(self, key_id, plaintext, encryption_context=None):
                    body = {
                        "KeyID": key_id,
                        "Plaintext": base64.b64encode(plaintext).decode("utf-8"),
                    }
                    if encryption_context:
                        body["EncryptionContext"] = encryption_context

                    res = self.json("Encrypt", {}, json.dumps(body))
                    if res == "":
                        raise Exception("empty response")
                    res_json = json.loads(res)
                    if "ResponseMetadata" in res_json and "Error" in res_json["ResponseMetadata"]:
                        raise Exception(f"KMS Error: {res_json['ResponseMetadata']['Error']}")
                    if "Result" in res_json and "CiphertextBlob" in res_json["Result"]:
                        return base64.b64decode(res_json["Result"]["CiphertextBlob"])
                    raise Exception(f"Unexpected response: {res_json}")

                def decrypt(self, ciphertext_blob, key_id, encryption_context=None):
                    body = {
                        "KeyID": key_id,
                        "CiphertextBlob": base64.b64encode(ciphertext_blob).decode("utf-8"),
                    }
                    if encryption_context:
                        body["EncryptionContext"] = encryption_context

                    res = self.json("Decrypt", {}, json.dumps(body))
                    if res == "":
                        raise Exception("empty response")
                    res_json = json.loads(res)
                    if "ResponseMetadata" in res_json and "Error" in res_json["ResponseMetadata"]:
                        raise Exception(f"KMS Error: {res_json['ResponseMetadata']['Error']}")
                    if "Result" in res_json and "Plaintext" in res_json["Result"]:
                        return base64.b64decode(res_json["Result"]["Plaintext"])
                    raise Exception(f"Unexpected response: {res_json}")

            self._kms_client = KmsService(
                self.region, self.access_key_id, self.secret_access_key, self.endpoint
            )
        return self._kms_client

    async def _encrypt_with_kms(self, plaintext: bytes) -> bytes:
        """
        Encrypt data with Volcengine KMS.

        Args:
            plaintext: Plaintext data

        Returns:
            Encrypted data
        """
        client = await self._get_kms_client()
        return await asyncio.to_thread(client.encrypt, self.key_id, plaintext)

    async def _decrypt_with_kms(self, ciphertext: bytes) -> bytes:
        """
        Decrypt data with Volcengine KMS.

        Args:
            ciphertext: Encrypted data

        Returns:
            Decrypted data
        """
        client = await self._get_kms_client()
        return await asyncio.to_thread(client.decrypt, ciphertext, self.key_id)

    async def _get_or_create_root_key(self) -> bytes:
        """
        Get or create root key.

        Returns:
            Root key bytes
        """
        if self._root_key is not None:
            return self._root_key

        try:
            # Try to read encrypted root key from file
            if self.key_file.exists():
                with open(self.key_file, "rb") as f:
                    encrypted_root_key = f.read()
                self._root_key = await self._decrypt_with_kms(encrypted_root_key)
                logger.info(f"Loaded existing root key from {self.key_file}")
                return self._root_key
        except Exception as e:
            raise ConfigError(
                f"Failed to load existing root key from {self.key_file}. Cannot continue: {e}"
            )

        # Generate new root key
        import secrets

        self._root_key = secrets.token_bytes(32)

        # Encrypt and store root key
        try:
            encrypted_root_key = await self._encrypt_with_kms(self._root_key)
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.key_file, "wb") as f:
                f.write(encrypted_root_key)
            logger.info(f"Created and stored new root key at {self.key_file}")
        except Exception as e:
            raise ConfigError(
                f"Failed to persist root key to file. "
                f"Refusing to start with ephemeral key (data loss risk): {e}"
            )

        return self._root_key

    async def get_root_key(self) -> bytes:
        """
        Get root key.

        Returns:
            Root key
        """
        if self._root_key is not None:
            _record_encryption_metrics(("record_key_cache_hit", {"provider": "volcengine_kms"}))
            return self._root_key

        _record_encryption_metrics(("record_key_cache_miss", {"provider": "volcengine_kms"}))

        start = time.perf_counter()
        status = "ok"
        try:
            self._root_key = await self._get_or_create_root_key()
            return self._root_key
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_key_load",
                    {
                        "status": status,
                        "provider": "volcengine_kms",
                        "duration_seconds": elapsed,
                    },
                ),
                ("record_key_version_usage", {"key_version": str(self.key_id)}),
                debug_message="Failed to record encryption key metrics for provider=volcengine_kms",
            )

    async def derive_account_key(self, account_id: str) -> bytes:
        """
        Derive Account Key using HKDF.

        Args:
            account_id: Account ID

        Returns:
            Derived Account Key
        """
        root_key = await self.get_root_key()
        return await self._hkdf_derive(root_key, account_id, HKDF_SALT, HKDF_INFO_PREFIX)


def create_root_key_provider(
    provider_type: str,
    config: Dict[str, Any],
) -> RootKeyProvider:
    """
    Create RootKeyProvider instance.

    Args:
        provider_type: Provider type ("local", "vault", "volcengine_kms")
        config: Configuration dictionary

    Returns:
        RootKeyProvider instance
    """
    if provider_type == "local":
        local_config = config.get("local", {})
        key_file_path = local_config.get("key_file", "~/.openviking/master.key")

        if not key_file_path:
            raise ConfigError("encryption.local.key_file is required")
        return LocalFileProvider(key_file_path)

    elif provider_type == "vault":
        vault_config = config.get("vault", {})
        address = vault_config.get("address")
        token = vault_config.get("token")
        mount_point = vault_config.get("mount_point", "transit")
        kv_mount_point = vault_config.get("kv_mount_point", "secret")
        kv_version = vault_config.get("kv_version", 1)
        root_key_name = vault_config.get("root_key_name", "openviking-root-key")
        encrypted_root_key_key = vault_config.get(
            "encrypted_root_key_key", "openviking-encrypted-root-key"
        )

        if not address or not token:
            raise ConfigError("vault.address and vault.token are required")
        return VaultProvider(
            address,
            token,
            mount_point,
            kv_mount_point,
            kv_version,
            root_key_name,
            encrypted_root_key_key,
        )

    elif provider_type == "volcengine_kms":
        volc_config = config.get("volcengine_kms", {})
        region = volc_config.get("region")
        access_key = volc_config.get("access_key")
        secret_key = volc_config.get("secret_key")
        key_id = volc_config.get("key_id")
        endpoint = volc_config.get("endpoint")
        key_file = volc_config.get("key_file")

        if not all([region, access_key, secret_key, key_id]):
            raise ConfigError("volcengine_kms region, access_key, secret_key, key_id are required")
        return VolcengineKMSProvider(
            region, access_key, secret_key, key_id, endpoint=endpoint, key_file=key_file
        )

    else:
        raise ConfigError(f"Unsupported provider type: {provider_type}")
