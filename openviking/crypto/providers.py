# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Key provider abstractions and implementations.

Provides multiple key management methods:
- LocalFileProvider: Local file storage for Root Key
- VaultProvider: HashiCorp Vault
- VolcengineKMSProvider: Volcengine KMS
"""

import abc
import os
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    ConfigError,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

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
    async def encrypt_file_key(self, plaintext_key: bytes, account_id: str) -> Any:
        """Encrypt File Key."""
        pass

    @abc.abstractmethod
    async def decrypt_file_key(self, encrypted_key: Any, account_id: str) -> bytes:
        """Decrypt File Key."""
        pass


class LocalFileProvider(RootKeyProvider):
    """Local file Root Key Provider."""

    def __init__(self, key_file: str):
        """
        Initialize LocalFileProvider.

        Args:
            key_file: Root Key file path
        """
        self.key_file = Path(key_file)
        self._root_key: Optional[bytes] = None

    async def get_root_key(self) -> bytes:
        """Get Root Key."""
        if self._root_key is None:
            self._root_key = await self._load_or_create_root_key()
        return self._root_key

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
        return await self._hkdf_derive(root_key, account_id)

    async def _hkdf_derive(self, root_key: bytes, account_id: str) -> bytes:
        """Derive key using HKDF."""
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF

            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=HKDF_SALT,
                info=HKDF_INFO_PREFIX + account_id.encode(),
            )
            return hkdf.derive(root_key)
        except ImportError:
            raise ConfigError("cryptography library is required for encryption")

    async def encrypt_file_key(self, plaintext_key: bytes, account_id: str) -> Tuple[bytes, bytes]:
        """
        Encrypt File Key.

        Returns:
            (encrypted_key, iv)
        """
        account_key = await self.derive_account_key(account_id)
        iv = secrets.token_bytes(12)
        encrypted_key = await self._aes_gcm_encrypt(account_key, iv, plaintext_key)
        return encrypted_key, iv

    async def decrypt_file_key(self, encrypted_key: bytes, iv: bytes, account_id: str) -> bytes:
        """Decrypt File Key."""
        account_key = await self.derive_account_key(account_id)
        return await self._aes_gcm_decrypt(account_key, iv, encrypted_key)

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


class VaultProvider(RootKeyProvider):
    """HashiCorp Vault Key Provider.

    Uses HashiCorp Vault's transit secrets engine for key management.
    Core features:
    - Root key management: Vault transit secrets engine
    - Account Key derivation: HKDF-SHA256 (from root key)
    - File Key encryption: Vault transit encryption API
    """

    SALT = b"OpenViking_KDF_Salt"
    INFO_PREFIX = b"OpenViking_Account_"
    ROOT_KEY_NAME = "openviking-root-key"

    def __init__(self, addr: str, token: str, mount_path: str = "transit"):
        """
        Initialize VaultProvider.

        Args:
            addr: Vault server address (e.g., "http://127.0.0.1:8200")
            token: Vault authentication token
            mount_path: Transit secrets engine mount path (default: "transit")
        """
        self.addr = addr
        self.token = token
        self.mount_path = mount_path
        self._client = None
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
            if not self._client.is_authenticated():
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
            self._client.sys.list_mounted_secrets_engines()
            if f"{self.mount_path}/" not in self._client.sys.list_mounted_secrets_engines()["data"]:
                self._client.sys.enable_secrets_engine(backend_type="transit", path=self.mount_path)
                logger.info(f"Enabled transit secrets engine at {self.mount_path}")
        except Exception as e:
            logger.warning(f"Failed to check/enable transit engine: {e}")

    async def _ensure_root_key_exists(self):
        """Ensure root key exists in Vault transit engine."""
        try:
            # Try to read the key
            self._client.secrets.transit.read_key(
                name=self.ROOT_KEY_NAME, mount_point=self.mount_path
            )
        except Exception:
            # Key doesn't exist, create it
            self._client.secrets.transit.create_key(
                name=self.ROOT_KEY_NAME, key_type="aes256-gcm96", mount_point=self.mount_path
            )
            logger.info(f"Created root key {self.ROOT_KEY_NAME} in Vault")

    async def get_root_key(self) -> bytes:
        """
        Get root key from Vault.

        Note: Since we can't export the key directly from Vault transit,
        we use a fixed seed and derive the root key.

        Returns:
            Root key
        """
        if self._root_key is None:
            # Use a fixed seed for root key derivation
            self._root_key = b"OpenViking_Vault_Root_Seed_Key_v1"
        return self._root_key

    async def derive_account_key(self, account_id: str) -> bytes:
        """
        Derive Account Key using HKDF.

        Args:
            account_id: Account ID

        Returns:
            Derived Account Key
        """
        root_key = await self.get_root_key()
        return await self._hkdf_derive(root_key, account_id)

    async def _hkdf_derive(self, root_key: bytes, account_id: str) -> bytes:
        """
        Derive key using HKDF.

        Args:
            root_key: Root key
            account_id: Account ID

        Returns:
            Derived key
        """
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF

            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self.SALT,
                info=self.INFO_PREFIX + account_id.encode(),
            )
            return hkdf.derive(root_key)
        except ImportError:
            raise ConfigError("cryptography library is required for encryption")

    async def encrypt_file_key(self, plaintext_key: bytes, account_id: str) -> bytes:
        """
        Encrypt File Key using Vault transit.

        Args:
            plaintext_key: Plaintext File Key
            account_id: Account ID (not used in Vault mode)

        Returns:
            Encrypted File Key as bytes
        """
        client = await self._get_client()

        import base64

        plaintext_b64 = base64.b64encode(plaintext_key).decode("utf-8")

        response = client.secrets.transit.encrypt_data(
            name=self.ROOT_KEY_NAME, plaintext=plaintext_b64, mount_point=self.mount_path
        )

        ciphertext_str = response["data"]["ciphertext"]
        return ciphertext_str.encode("utf-8")

    async def decrypt_file_key(self, encrypted_key: bytes, account_id: str) -> bytes:
        """
        Decrypt File Key using Vault transit.

        Args:
            encrypted_key: Encrypted File Key (ciphertext bytes from Vault)
            account_id: Account ID (not used in Vault mode)

        Returns:
            Decrypted File Key
        """
        client = await self._get_client()

        ciphertext_str = encrypted_key.decode("utf-8")

        response = client.secrets.transit.decrypt_data(
            name=self.ROOT_KEY_NAME, ciphertext=ciphertext_str, mount_point=self.mount_path
        )

        import base64

        return base64.b64decode(response["data"]["plaintext"])


class VolcengineKMSProvider(RootKeyProvider):
    """Volcengine KMS Key Provider.

    Suitable for production environments, using Volcengine KMS service for key management.
    Core features:
    - Root key storage: Volcengine KMS
    - Account Key derivation: HKDF-SHA256
    - File Key encryption: Volcengine KMS encryption API
    """

    SALT = b"OpenViking_KDF_Salt"
    INFO_PREFIX = b"OpenViking_Account_"

    def __init__(
        self,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        key_id: str,
        endpoint: Optional[str] = None,
    ):
        """
        Initialize Volcengine KMS Provider.

        Args:
            region: Region (e.g., cn-beijing)
            access_key_id: Volcengine Access Key ID
            secret_access_key: Volcengine Secret Access Key
            key_id: Volcengine KMS Key ID (immutable system-generated identifier)
            endpoint: Custom KMS service endpoint (optional)
        """
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.key_id = key_id
        self.endpoint = endpoint or f"kms.{region}.volcengineapi.com"
        self._kms_client = None

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

                def encrypt(self, key_id, plaintext):
                    body = {
                        "KeyID": key_id,
                        "Plaintext": base64.b64encode(plaintext).decode("utf-8"),
                    }

                    res = self.json("Encrypt", {}, json.dumps(body))
                    if res == "":
                        raise Exception("empty response")
                    res_json = json.loads(res)
                    if "ResponseMetadata" in res_json and "Error" in res_json["ResponseMetadata"]:
                        raise Exception(f"KMS Error: {res_json['ResponseMetadata']['Error']}")
                    if "Result" in res_json and "CiphertextBlob" in res_json["Result"]:
                        return base64.b64decode(res_json["Result"]["CiphertextBlob"])
                    raise Exception(f"Unexpected response: {res_json}")

                def decrypt(self, ciphertext_blob, key_id):
                    body = {
                        "KeyID": key_id,
                        "CiphertextBlob": base64.b64encode(ciphertext_blob).decode("utf-8"),
                    }

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

    async def get_root_key(self) -> bytes:
        """
        Get root key (only used for deriving Account Key).

        Note: In Volcengine KMS, the root key is managed by KMS service.
        Here we return the base key used for deriving Account Key.

        Returns:
            Base key
        """
        return b"OpenViking_Volcengine_KMS_Root_Seed"

    async def derive_account_key(self, account_id: str) -> bytes:
        """
        Derive Account Key using HKDF.

        Args:
            account_id: Account ID

        Returns:
            Derived Account Key
        """
        root_key = await self.get_root_key()
        return await self._hkdf_derive(root_key, account_id)

    async def _hkdf_derive(self, root_key: bytes, account_id: str) -> bytes:
        """
        Derive key using HKDF.

        Args:
            root_key: Root key
            account_id: Account ID

        Returns:
            Derived key
        """
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF

            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self.SALT,
                info=self.INFO_PREFIX + account_id.encode(),
            )
            return hkdf.derive(root_key)
        except ImportError:
            raise ConfigError("cryptography library is required for encryption")

    async def encrypt_file_key(self, plaintext_key: bytes, account_id: str) -> bytes:
        """
        Encrypt File Key using Volcengine KMS.

        Args:
            plaintext_key: Plaintext File Key
            account_id: Account ID (not used in Volcengine KMS mode)

        Returns:
            Encrypted File Key
        """
        client = await self._get_kms_client()
        return client.encrypt(self.key_id, plaintext_key)

    async def decrypt_file_key(self, encrypted_key: bytes, account_id: str) -> bytes:
        """
        Decrypt File Key using Volcengine KMS.

        Args:
            encrypted_key: Encrypted File Key
            account_id: Account ID (not used in Volcengine KMS mode)

        Returns:
            Decrypted File Key
        """
        client = await self._get_kms_client()
        return client.decrypt(encrypted_key, self.key_id)


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

        if not address or not token:
            raise ConfigError("vault.address and vault.token are required")
        return VaultProvider(address, token, mount_point)

    elif provider_type == "volcengine_kms":
        volc_config = config.get("volcengine_kms", {})
        region = volc_config.get("region")
        access_key = volc_config.get("access_key")
        secret_key = volc_config.get("secret_key")
        key_id = volc_config.get("key_id")

        if not all([region, access_key, secret_key, key_id]):
            raise ConfigError("volcengine_kms region, access_key, secret_key, key_id are required")
        return VolcengineKMSProvider(region, access_key, secret_key, key_id)

    else:
        raise ConfigError(f"Unsupported provider type: {provider_type}")
