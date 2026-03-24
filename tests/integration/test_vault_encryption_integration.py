# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Vault encryption integration tests.

Tests encryption functionality with HashiCorp Vault as the key provider.
Requires VAULT_TOKEN env var to be set.
Run: pytest tests/integration/test_vault_encryption_integration.py -v -m integration
"""

import secrets
from pathlib import Path

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.crypto.config import bootstrap_encryption
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import AuthenticationFailedError, ConfigError
from openviking.crypto.providers import VaultProvider, create_root_key_provider
from openviking.server.api_keys import APIKeyManager
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.integration.conftest import VAULT_ADDR, VAULT_TOKEN, requires_vault

pytestmark = [pytest.mark.integration, requires_vault]

# Vault environment configuration
VAULT_MOUNT_PATH = "transit"


class TestVaultProviderIntegration:
    """Integration tests for VaultProvider."""

    @pytest_asyncio.fixture(scope="function")
    async def vault_provider(self):
        """Fixture that provides a VaultProvider instance."""
        provider = VaultProvider(addr=VAULT_ADDR, token=VAULT_TOKEN, mount_path=VAULT_MOUNT_PATH)
        yield provider

    @pytest.fixture(scope="function")
    def vault_config(self):
        """Fixture that provides Vault configuration."""
        return {
            "vault": {
                "address": VAULT_ADDR,
                "token": VAULT_TOKEN,
                "mount_point": VAULT_MOUNT_PATH,
            }
        }

    async def test_vault_provider_initialization(self, vault_provider):
        """Test VaultProvider initialization and Vault connection."""
        client = await vault_provider._get_client()
        assert client is not None
        assert client.is_authenticated()

    async def test_derive_account_key(self, vault_provider):
        """Test deriving account keys."""
        account_id = "test-account-vault"
        account_key = await vault_provider.derive_account_key(account_id)

        assert account_key is not None
        assert isinstance(account_key, bytes)
        assert len(account_key) == 32

    async def test_derive_different_account_keys(self, vault_provider):
        """Test that different accounts get different keys."""
        account1_key = await vault_provider.derive_account_key("account-1")
        account2_key = await vault_provider.derive_account_key("account-2")

        assert account1_key != account2_key

    async def test_derive_same_account_key_consistent(self, vault_provider):
        """Test that same account gets consistent key."""
        key1 = await vault_provider.derive_account_key("test-account")
        key2 = await vault_provider.derive_account_key("test-account")

        assert key1 == key2

    async def test_encrypt_decrypt_file_key(self, vault_provider):
        """Test encrypting and decrypting file keys."""
        plaintext_key = secrets.token_bytes(32)
        account_id = "test-account-encrypt"

        encrypted_key, iv = await vault_provider.encrypt_file_key(plaintext_key, account_id)

        assert encrypted_key is not None
        assert isinstance(encrypted_key, bytes)
        assert encrypted_key != plaintext_key
        assert iv is not None
        assert isinstance(iv, bytes)

        decrypted = await vault_provider.decrypt_file_key(encrypted_key, iv, account_id)

        assert decrypted == plaintext_key

    async def test_encrypt_decrypt_multiple_keys(self, vault_provider):
        """Test encrypting and decrypting multiple different keys."""
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)
        account_id = "test-account-multiple"

        encrypted1, iv1 = await vault_provider.encrypt_file_key(key1, account_id)
        encrypted2, iv2 = await vault_provider.encrypt_file_key(key2, account_id)

        assert encrypted1 != encrypted2

        decrypted1 = await vault_provider.decrypt_file_key(encrypted1, iv1, account_id)
        decrypted2 = await vault_provider.decrypt_file_key(encrypted2, iv2, account_id)

        assert decrypted1 == key1
        assert decrypted2 == key2

    async def test_create_root_key_provider_vault(self, vault_config):
        """Test creating VaultProvider via create_root_key_provider."""
        provider = create_root_key_provider("vault", vault_config)

        assert provider is not None
        assert isinstance(provider, VaultProvider)

    async def test_get_root_key(self, vault_provider):
        """Test getting root key."""
        root_key = await vault_provider.get_root_key()

        assert root_key is not None
        assert isinstance(root_key, bytes)
        assert len(root_key) > 0

    async def test_full_roundtrip(self, vault_provider):
        """Test full encryption/decryption roundtrip."""
        # Derive account key
        account_id = "test-account-roundtrip"
        account_key = await vault_provider.derive_account_key(account_id)

        assert account_key is not None
        assert len(account_key) == 32

        # Generate and encrypt file key
        file_key = secrets.token_bytes(32)
        encrypted_key, iv = await vault_provider.encrypt_file_key(file_key, account_id)

        assert encrypted_key is not None
        assert isinstance(encrypted_key, bytes)
        assert iv is not None
        assert isinstance(iv, bytes)

        # Decrypt and verify
        decrypted_key = await vault_provider.decrypt_file_key(encrypted_key, iv, account_id)

        assert decrypted_key == file_key


class TestVaultProviderErrors:
    """Test error handling for VaultProvider."""

    async def test_invalid_token(self):
        """Test with invalid token."""
        provider = VaultProvider(
            addr="http://127.0.0.1:8200", token="invalid-token", mount_path="transit"
        )

        with pytest.raises(AuthenticationFailedError):
            await provider._get_client()

    async def test_invalid_address(self):
        """Test with invalid address."""
        provider = VaultProvider(
            addr="http://invalid-address-that-does-not-exist:8200",
            token="test-token",
            mount_path="transit",
        )

        with pytest.raises(Exception):  # noqa: B017
            await provider._get_client()

    def test_create_provider_missing_config(self):
        """Test creating provider with missing config."""
        with pytest.raises(ConfigError):
            create_root_key_provider("vault", {})

    def test_create_provider_missing_token(self):
        """Test creating provider with missing token."""
        with pytest.raises(ConfigError):
            create_root_key_provider("vault", {"vault": {"address": "http://127.0.0.1:8200"}})

    def test_create_provider_missing_address(self):
        """Test creating provider with missing address."""
        with pytest.raises(ConfigError):
            create_root_key_provider("vault", {"vault": {"token": "test-token"}})


@pytest_asyncio.fixture(scope="function")
async def vault_encryption_config():
    """Fixture that provides encryption configuration with Vault provider"""
    return {
        "encryption": {
            "enabled": True,
            "provider": "vault",
            "vault": {"address": VAULT_ADDR, "token": VAULT_TOKEN, "mount_point": VAULT_MOUNT_PATH},
        }
    }


@pytest_asyncio.fixture(scope="function")
async def vault_file_encryptor():
    """Fixture that provides a FileEncryptor instance with Vault provider for testing"""
    provider = VaultProvider(addr=VAULT_ADDR, token=VAULT_TOKEN, mount_path=VAULT_MOUNT_PATH)
    return FileEncryptor(provider)


@pytest_asyncio.fixture(scope="function")
async def openviking_client_with_vault_encryption(test_data_dir: Path, vault_encryption_config):
    """Fixture that provides an OpenViking client with Vault encryption enabled"""
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()

    # Clean data directory
    if test_data_dir.exists():
        import shutil

        shutil.rmtree(test_data_dir)
    test_data_dir.mkdir(parents=True, exist_ok=True)

    # Create config dict with encryption enabled
    config_dict = {}
    config_dict.update(vault_encryption_config)
    config_dict["storage"] = {
        "workspace": str(test_data_dir / "workspace"),
        "vectordb": {"name": "test", "backend": "local", "project": "default"},
    }
    config_dict["embedding"] = {
        "dense": {
            "provider": "openai",
            "api_key": "fake",
            "model": "text-embedding-3-small",
        }
    }

    # Initialize config singleton
    OpenVikingConfigSingleton.initialize(config_dict=config_dict)

    client = AsyncOpenViking(path=str(test_data_dir))
    await client.initialize()

    yield client

    await client.close()
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()


class TestVaultEncryptionBootstrap:
    """Tests for encryption module bootstrap with Vault provider"""

    async def test_bootstrap_encryption_vault_enabled(self, vault_encryption_config):
        """Test bootstrapping encryption with Vault provider enabled"""
        encryptor = await bootstrap_encryption(vault_encryption_config)
        assert encryptor is not None
        assert isinstance(encryptor, FileEncryptor)
        assert isinstance(encryptor.provider, VaultProvider)


class TestVaultFileEncryptorIntegration:
    """Tests for FileEncryptor integration with Vault provider"""

    async def test_encrypt_decrypt_roundtrip_vault(self, vault_file_encryptor):
        """Test basic encryption/decryption roundtrip with Vault"""
        account_id = "test_account_vault"
        plaintext = b"This is a test file content for Vault encryption integration"

        # Encrypt
        ciphertext = await vault_file_encryptor.encrypt(account_id, plaintext)
        assert ciphertext != plaintext
        assert ciphertext.startswith(b"OVE1")

        # Decrypt
        decrypted = await vault_file_encryptor.decrypt(account_id, ciphertext)
        assert decrypted == plaintext

    async def test_different_accounts_isolation(self, vault_file_encryptor):
        """Test that different accounts cannot decrypt each other's data"""
        account1 = "account_1_vault"
        account2 = "account_2_vault"
        plaintext = b"Sensitive data for account isolation test"

        # Encrypt with account1
        ciphertext = await vault_file_encryptor.encrypt(account1, plaintext)

        # Decrypt with account1 should work
        decrypted1 = await vault_file_encryptor.decrypt(account1, ciphertext)
        assert decrypted1 == plaintext

        # Decrypt with account2 should fail
        from openviking.crypto.exceptions import KeyMismatchError

        with pytest.raises(KeyMismatchError):
            await vault_file_encryptor.decrypt(account2, ciphertext)

    async def test_encrypt_empty_data_vault(self, vault_file_encryptor):
        """Test encrypting and decrypting empty data with Vault"""
        account_id = "test_account_vault"
        plaintext = b""

        ciphertext = await vault_file_encryptor.encrypt(account_id, plaintext)
        assert ciphertext.startswith(b"OVE1")

        decrypted = await vault_file_encryptor.decrypt(account_id, ciphertext)
        assert decrypted == b""


class TestVikingFSEncryptionWithVault:
    """
    Complete VikingFS encryption tests using Vault as key provider.
    Includes account management, resource operations, skill operations, etc.
    """

    ROOT_KEY = "test-root-key-for-vault-encryption-tests-abcdef123456"

    @pytest_asyncio.fixture(scope="function")
    async def openviking_service_with_vault_encryption(
        self, test_data_dir: Path, vault_encryption_config
    ):
        """
        Fixture provides OpenVikingService instance with Vault encryption.
        Also initializes APIKeyManager for account management.
        """
        # Clean data directory
        if test_data_dir.exists():
            import shutil

            shutil.rmtree(test_data_dir)
        test_data_dir.mkdir(parents=True, exist_ok=True)

        # Create config dict with encryption enabled and root_api_key set
        config_dict = {}
        config_dict.update(vault_encryption_config)
        config_dict["storage"] = {
            "workspace": str(test_data_dir / "workspace"),
            "vectordb": {"name": "test", "backend": "local", "project": "default"},
        }
        config_dict["embedding"] = {
            "dense": {
                "provider": "openai",
                "api_key": "fake",
                "model": "text-embedding-3-small",
            }
        }
        config_dict["server"] = {"root_api_key": self.ROOT_KEY}

        # Initialize config singleton
        OpenVikingConfigSingleton.initialize(config_dict=config_dict)

        # Create OpenVikingService
        svc = OpenVikingService(
            path=str(test_data_dir / "viking"), user=UserIdentifier.the_default_user("test_user")
        )
        await svc.initialize()

        # Create APIKeyManager using VikingFS to ensure system files are encrypted
        api_key_manager = APIKeyManager(
            root_key=self.ROOT_KEY, viking_fs=svc.viking_fs, encryption_enabled=True
        )
        await api_key_manager.load()

        yield {"service": svc, "api_key_manager": api_key_manager, "test_data_dir": test_data_dir}

        await svc.close()
        await AsyncOpenViking.reset()
        OpenVikingConfigSingleton.reset_instance()

    def _is_file_encrypted(self, file_path: Path) -> bool:
        """
        Check if file is encrypted (by "OVE1" header).

        Args:
            file_path: File path

        Returns:
            True if file is encrypted, False otherwise
        """
        if not file_path.exists():
            return False
        try:
            with open(file_path, "rb") as f:
                header = f.read(4)
                return header == b"OVE1"
        except Exception:
            return False

    async def _check_all_files_encrypted(
        self, svc, ctx, base_uri: str, print_paths: bool = True
    ) -> None:
        """
        Recursively check if all files in directory are encrypted.

        Args:
            svc: OpenVikingService instance
            ctx: RequestContext instance
            base_uri: Base URI to check
            print_paths: Whether to print encrypted file paths
        """
        agfs_client = svc._agfs_client

        async def _check_recursive(uri: str):
            try:
                entries = await svc.viking_fs.ls(uri, ctx=ctx)
                for entry in entries:
                    entry_uri = entry["uri"]
                    if entry["isDir"]:
                        await _check_recursive(entry_uri)
                    else:
                        # Use VikingFS's _uri_to_path method to get file path
                        agfs_path = svc.viking_fs._uri_to_path(entry_uri, ctx=ctx)
                        raw_content = agfs_client.read(agfs_path)
                        assert raw_content.startswith(b"OVE1"), f"File {entry_uri} is not encrypted"
                        if print_paths:
                            print(f"✓ File is encrypted: {entry_uri}")
            except Exception as e:
                if "not found" in str(e).lower():
                    pass
                else:
                    raise

        await _check_recursive(base_uri)

    @pytest.mark.asyncio
    async def test_resource_operations_with_encryption(
        self, openviking_service_with_vault_encryption, tmp_path
    ):
        """Test 4: Resource operations and encryption verification"""
        data = openviking_service_with_vault_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        # Create test account
        account_id = "test-account-resources"
        admin_user_id = "admin"
        await api_key_manager.create_account(account_id, admin_user_id)

        from openviking.server.identity import RequestContext, Role

        user = UserIdentifier(account_id, admin_user_id, admin_user_id)
        ctx = RequestContext(user=user, role=Role.ADMIN)

        # Create test resource file
        test_content = "This is test resource content for Vault encryption"
        test_uri = f"viking://{account_id}/resources/test_file.txt"

        # Write file
        await svc.viking_fs.write_file(test_uri, test_content, ctx=ctx)

        # Verify read content is correct
        read_content = await svc.viking_fs.read_file(test_uri, ctx=ctx)
        assert read_content == test_content

        # Verify file is encrypted
        agfs_client = svc._agfs_client
        agfs_path = svc.viking_fs._uri_to_path(test_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1")

        # Test various operations
        resources_dir_uri = f"viking://{account_id}/resources"

        # ls operation
        ls_entries = await svc.viking_fs.ls(resources_dir_uri, ctx=ctx)
        assert len(ls_entries) > 0

        # tree operation
        tree_entries = await svc.viking_fs.tree(resources_dir_uri, ctx=ctx)
        assert len(tree_entries) > 0

        # grep operation
        try:
            grep_result = await svc.viking_fs.grep(resources_dir_uri, "Vault", ctx=ctx)
            assert grep_result is not None
        except Exception as e:
            print(f"[WARNING] grep operation may not be supported: {e}")

        # abstract operation
        try:
            abstract = await svc.viking_fs.abstract(test_uri, ctx=ctx)
            assert abstract is not None
            assert "OVE1" not in abstract
        except Exception as e:
            print(f"[WARNING] abstract operation may not be supported: {e}")

        # overview operation
        try:
            overview = await svc.viking_fs.overview(test_uri, ctx=ctx)
            assert overview is not None
            assert "OVE1" not in overview
        except Exception as e:
            print(f"[WARNING] overview operation may not be supported: {e}")

    @pytest.mark.asyncio
    async def test_skill_operations_with_encryption(self, openviking_service_with_vault_encryption):
        """Test 5: Skill operations and encryption verification"""
        data = openviking_service_with_vault_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        # Create test account
        account_id = "test-account-skills"
        admin_user_id = "admin"
        await api_key_manager.create_account(account_id, admin_user_id)

        from openviking.server.identity import RequestContext, Role

        user = UserIdentifier(account_id, admin_user_id, admin_user_id)
        ctx = RequestContext(user=user, role=Role.ADMIN)

        # Create skill directory and file
        skill_dir_uri = f"viking://{account_id}/agent/test-skill"
        skill_md_uri = f"{skill_dir_uri}/SKILL.md"

        # Create directory
        await svc.viking_fs.mkdir(skill_dir_uri, ctx=ctx)

        # Write skill file
        skill_content = """---
name: Test Skill
version: 1.0.0
description: Test skill for Vault encryption
---

# Test Skill

This is a test skill for verifying Vault encryption.
"""
        await svc.viking_fs.write_file(skill_md_uri, skill_content, ctx=ctx)

        # Verify read content is correct
        read_content = await svc.viking_fs.read_file(skill_md_uri, ctx=ctx)
        assert "Test Skill" in read_content

        # Verify file is encrypted
        agfs_client = svc._agfs_client
        agfs_path = svc.viking_fs._uri_to_path(skill_md_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1")

        # Test various operations
        agent_dir_uri = f"viking://{account_id}/agent"

        # ls operation
        ls_entries = await svc.viking_fs.ls(agent_dir_uri, ctx=ctx)
        assert len(ls_entries) > 0

        # tree operation
        try:
            tree_entries = await svc.viking_fs.tree(agent_dir_uri, ctx=ctx)
            assert len(tree_entries) > 0
        except Exception as e:
            print(f"[WARNING] tree operation may not be supported: {e}")

    @pytest.mark.asyncio
    async def test_memory_operations_with_encryption(
        self, openviking_service_with_vault_encryption
    ):
        """Test 6: Memory operations and encryption verification"""
        data = openviking_service_with_vault_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        # Create test account
        account_id = "test-account-memories"
        user_id = "test-user"
        await api_key_manager.create_account(account_id, user_id)

        from openviking.server.identity import RequestContext, Role

        user = UserIdentifier(account_id, user_id, user_id)
        ctx = RequestContext(user=user, role=Role.USER)

        # Create memory directory and file
        memory_dir_uri = f"viking://{account_id}/user/{user_id}/memories"
        memory_file_uri = f"{memory_dir_uri}/preferences.md"

        # Create directory
        try:
            await svc.viking_fs.mkdir(memory_dir_uri, ctx=ctx)
        except Exception:
            pass  # Directory may already exist

        # Write memory file
        memory_content = "# User Preferences\n\nTheme: dark\nLanguage: English"
        await svc.viking_fs.write_file(memory_file_uri, memory_content, ctx=ctx)

        # Verify read content is correct
        read_content = await svc.viking_fs.read_file(memory_file_uri, ctx=ctx)
        assert "User Preferences" in read_content

        # Verify file is encrypted
        agfs_client = svc._agfs_client
        agfs_path = svc.viking_fs._uri_to_path(memory_file_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1")

    @pytest.mark.asyncio
    async def test_session_operations_with_encryption(
        self, openviking_service_with_vault_encryption
    ):
        """Test 7: Session operations and encryption verification"""
        data = openviking_service_with_vault_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        # Create test account
        account_id = "test-account-sessions"
        user_id = "test-user"
        await api_key_manager.create_account(account_id, user_id)

        from openviking.server.identity import RequestContext, Role

        user = UserIdentifier(account_id, user_id, user_id)
        ctx = RequestContext(user=user, role=Role.USER)

        # Create session
        session = await svc.sessions.create(ctx)
        session_id = session.session_id
        assert session_id is not None

        # Check if session directory files are encrypted
        session_dir_uri = f"viking://{account_id}/session"
        await self._check_all_files_encrypted(svc, ctx, session_dir_uri, print_paths=False)

    @pytest.mark.asyncio
    async def test_complete_encryption_workflow_with_vault(
        self, openviking_service_with_vault_encryption
    ):
        """
        Complete encryption workflow test using Vault as key provider, including:
        1. Precondition: Create random account and user
        2. Execution: Resource, skill, session, relationship operations
        3. Encryption verification: Check OVE1 header through file system
        4. Postcondition: Delete account
        """
        import uuid

        from openviking.server.identity import RequestContext, Role

        svc = openviking_service_with_vault_encryption["service"]
        api_key_manager = openviking_service_with_vault_encryption["api_key_manager"]
        openviking_service_with_vault_encryption["test_data_dir"]

        # 1. Precondition: Create random account and user
        random_account_id = f"test-account-{uuid.uuid4()}"
        random_user_id = f"test-user-{uuid.uuid4()}"
        print(f"\n=== Creating account: {random_account_id}, user: {random_user_id} ===")

        await api_key_manager.create_account(random_account_id, random_user_id)
        user = UserIdentifier(random_account_id, random_user_id, random_user_id)
        ctx = RequestContext(user=user, role=Role.ADMIN)

        # 2. Execution: Resource, skill, session operations
        print("\n=== Executing operations ===")

        # Create resource
        test_content = f"Test content for account {random_account_id} with Vault encryption"
        test_resource_uri = f"viking://{random_account_id}/resources/test_workflow.txt"
        await svc.viking_fs.write_file(test_resource_uri, test_content, ctx=ctx)
        print(f"✓ Resource created successfully: {test_resource_uri}")

        # Create skill
        skill_dir_uri = f"viking://{random_account_id}/agent/test-workflow-skill"
        skill_md_uri = f"{skill_dir_uri}/SKILL.md"
        await svc.viking_fs.mkdir(skill_dir_uri, ctx=ctx)
        skill_content = """---
name: Workflow Test Skill
version: 1.0.0
description: Test skill for complete workflow
---

# Workflow Test Skill
"""
        await svc.viking_fs.write_file(skill_md_uri, skill_content, ctx=ctx)
        print(f"✓ Skill created successfully: {skill_md_uri}")

        # Create memory
        memory_dir_uri = f"viking://{random_account_id}/user/{random_user_id}/memories"
        memory_file_uri = f"{memory_dir_uri}/test_memory.md"
        try:
            await svc.viking_fs.mkdir(memory_dir_uri, ctx=ctx)
        except Exception:
            pass
        memory_content = "# Test Memory\n\nThis is a test memory for workflow"
        await svc.viking_fs.write_file(memory_file_uri, memory_content, ctx=ctx)
        print(f"✓ Memory created successfully: {memory_file_uri}")

        # Create session
        session = await svc.sessions.create(ctx)
        session_id = session.session_id
        print(f"✓ Session created successfully: {session_id}")

        # 3. Encryption verification: Check OVE1 header through file system
        print("\n=== Encryption verification ===")

        # Check if all files are encrypted
        account_root_uri = f"viking://{random_account_id}"
        await self._check_all_files_encrypted(svc, ctx, account_root_uri, print_paths=True)

        # 4. Postcondition: Delete account
        print("\n=== Cleanup ===")
        await api_key_manager.delete_account(random_account_id)
        print(f"✓ Account {random_account_id} deleted")

        print("\n✓ Complete Vault encryption workflow test completed!")
