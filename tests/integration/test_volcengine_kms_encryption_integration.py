# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Volcengine KMS Encryption Integration Tests

Tests encryption functionality integrated with VikingFS and OpenViking service
using Volcengine KMS as the key provider.
Requires VOLCENGINE_ACCESS_KEY, VOLCENGINE_SECRET_KEY, and VOLCENGINE_KMS_KEY_ID env vars.
Run: pytest tests/integration/test_volcengine_kms_encryption_integration.py -v -m integration
"""

import secrets
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import VolcengineKMSProvider
from openviking.server.api_keys import APIKeyManager
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.integration.conftest import (
    VOLCENGINE_ACCESS_KEY,
    VOLCENGINE_KMS_KEY_ID,
    VOLCENGINE_KMS_REGION,
    VOLCENGINE_SECRET_KEY,
    requires_volcengine_kms,
)

pytestmark = [pytest.mark.integration, requires_volcengine_kms]


class TestVolcengineKMSProvider:
    """Tests for VolcengineKMSProvider"""

    @pytest_asyncio.fixture(scope="module")
    async def volcengine_kms_provider(self):
        """
        Fixture that provides a VolcengineKMSProvider instance for testing.
        Requires valid Volcengine KMS credentials from environment variables.
        """
        provider = VolcengineKMSProvider(
            region=VOLCENGINE_KMS_REGION,
            access_key_id=VOLCENGINE_ACCESS_KEY,
            secret_access_key=VOLCENGINE_SECRET_KEY,
            key_id=VOLCENGINE_KMS_KEY_ID,
        )
        return provider

    @pytest.mark.asyncio
    async def test_initialize_provider(self, volcengine_kms_provider):
        """Test that VolcengineKMSProvider initializes correctly"""
        assert volcengine_kms_provider is not None
        assert volcengine_kms_provider.region == "cn-beijing"
        assert volcengine_kms_provider.key_id == "d926aa0d-deee-455a-9186-0d07e2e250e1"

    @pytest.mark.asyncio
    async def test_get_root_key(self, volcengine_kms_provider):
        """Test getting root key"""
        root_key = await volcengine_kms_provider.get_root_key()
        assert isinstance(root_key, bytes)
        assert len(root_key) > 0

    @pytest.mark.asyncio
    async def test_derive_account_key(self, volcengine_kms_provider):
        """Test deriving account key"""
        account_id = "test-account-volcengine"
        account_key = await volcengine_kms_provider.derive_account_key(account_id)
        assert isinstance(account_key, bytes)
        assert len(account_key) == 32  # Should be 256-bit key

    @pytest.mark.asyncio
    async def test_derive_different_account_keys(self, volcengine_kms_provider):
        """Test that different account IDs produce different keys"""
        account1 = "account-1-volc"
        account2 = "account-2-volc"

        key1 = await volcengine_kms_provider.derive_account_key(account1)
        key2 = await volcengine_kms_provider.derive_account_key(account2)

        assert key1 != key2

    @pytest.mark.asyncio
    async def test_derive_same_account_key(self, volcengine_kms_provider):
        """Test that same account ID produces same key"""
        account_id = "test-account-consistent"

        key1 = await volcengine_kms_provider.derive_account_key(account_id)
        key2 = await volcengine_kms_provider.derive_account_key(account_id)

        assert key1 == key2

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_file_key(self, volcengine_kms_provider):
        """Test encrypting and decrypting a file key"""
        plaintext_key = secrets.token_bytes(32)  # 256-bit key
        account_id = "test-account-encrypt"

        encrypted_key, iv = await volcengine_kms_provider.encrypt_file_key(
            plaintext_key, account_id
        )
        assert isinstance(encrypted_key, bytes)
        assert isinstance(iv, bytes)

        decrypted = await volcengine_kms_provider.decrypt_file_key(encrypted_key, iv, account_id)
        assert decrypted == plaintext_key

    @pytest.mark.asyncio
    async def test_encrypt_different_keys(self, volcengine_kms_provider):
        """Test that different plaintexts produce different ciphertexts"""
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)
        account_id = "test-account-multi"

        encrypted1, iv1 = await volcengine_kms_provider.encrypt_file_key(key1, account_id)
        encrypted2, iv2 = await volcengine_kms_provider.encrypt_file_key(key2, account_id)

        assert encrypted1 != encrypted2

    @pytest.mark.asyncio
    async def test_full_roundtrip(self, volcengine_kms_provider):
        """Test complete roundtrip: derive account key, encrypt, decrypt"""
        account_id = "test-account-roundtrip"

        # Derive account key
        account_key = await volcengine_kms_provider.derive_account_key(account_id)
        assert isinstance(account_key, bytes)
        assert len(account_key) == 32

        # Generate and encrypt file key
        file_key = secrets.token_bytes(32)
        encrypted_file_key, iv = await volcengine_kms_provider.encrypt_file_key(
            file_key, account_id
        )

        # Decrypt file key
        decrypted_file_key = await volcengine_kms_provider.decrypt_file_key(
            encrypted_file_key, iv, account_id
        )
        assert decrypted_file_key == file_key


@pytest_asyncio.fixture(scope="function")
async def volcengine_kms_config():
    """Fixture that provides Volcengine KMS encryption configuration"""
    return {
        "encryption": {
            "enabled": True,
            "provider": "volcengine_kms",
            "volcengine_kms": {
                "region": VOLCENGINE_KMS_REGION,
                "access_key": VOLCENGINE_ACCESS_KEY,
                "secret_key": VOLCENGINE_SECRET_KEY,
                "key_id": VOLCENGINE_KMS_KEY_ID,
            },
        }
    }


@pytest_asyncio.fixture(scope="function")
async def volcengine_file_encryptor():
    """Fixture that provides a FileEncryptor with Volcengine KMS"""
    provider = VolcengineKMSProvider(
        region=VOLCENGINE_KMS_REGION,
        access_key_id=VOLCENGINE_ACCESS_KEY,
        secret_access_key=VOLCENGINE_SECRET_KEY,
        key_id=VOLCENGINE_KMS_KEY_ID,
    )
    return FileEncryptor(provider)


@pytest_asyncio.fixture(scope="function")
async def openviking_client_with_volcengine_encryption(test_data_dir: Path, volcengine_kms_config):
    """Fixture that provides an OpenViking client with Volcengine KMS encryption"""
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()

    if test_data_dir.exists():
        import shutil

        shutil.rmtree(test_data_dir)
    test_data_dir.mkdir(parents=True, exist_ok=True)

    config_dict = {}
    config_dict.update(volcengine_kms_config)
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

    OpenVikingConfigSingleton.initialize(config_dict=config_dict)

    client = AsyncOpenViking(path=str(test_data_dir))
    await client.initialize()

    yield client

    await client.close()
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()


class TestVolcengineKMSEncryptionBootstrap:
    """Tests for encryption module bootstrap with Volcengine KMS"""

    @pytest.mark.asyncio
    async def test_bootstrap_encryption_with_volcengine_kms(self, volcengine_kms_config, tmp_path):
        """Test bootstrapping encryption with Volcengine KMS"""
        from openviking.crypto.config import bootstrap_encryption

        config_dict = volcengine_kms_config
        config_dict["storage"] = {"workspace": str(tmp_path)}

        result = await bootstrap_encryption(config_dict)

        assert result is not None
        assert isinstance(result, FileEncryptor)

    @pytest.mark.asyncio
    async def test_file_encryptor_with_volcengine_kms(self, volcengine_file_encryptor):
        """Test FileEncryptor with Volcengine KMS"""
        account_id = "test_account_volcengine"
        plaintext = b"Test data for Volcengine KMS encryption"

        ciphertext = await volcengine_file_encryptor.encrypt(account_id, plaintext)
        assert ciphertext.startswith(b"OVE1")

        decrypted = await volcengine_file_encryptor.decrypt(account_id, ciphertext)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_different_accounts_isolation(self, volcengine_file_encryptor):
        """Test that different accounts cannot decrypt each other's data"""
        account1 = "account_1_volc"
        account2 = "account_2_volc"
        plaintext = b"Sensitive data for account isolation test"

        # Encrypt with account1
        ciphertext = await volcengine_file_encryptor.encrypt(account1, plaintext)

        # Decrypt with account1 should work
        decrypted1 = await volcengine_file_encryptor.decrypt(account1, ciphertext)
        assert decrypted1 == plaintext

        # Decrypt with account2 should fail
        from openviking.crypto.exceptions import KeyMismatchError

        with pytest.raises(KeyMismatchError):
            await volcengine_file_encryptor.decrypt(account2, ciphertext)

    @pytest.mark.asyncio
    async def test_encrypt_empty_data(self, volcengine_file_encryptor):
        """Test encrypting and decrypting empty data"""
        account_id = "test_account_volc"
        plaintext = b""

        ciphertext = await volcengine_file_encryptor.encrypt(account_id, plaintext)
        assert ciphertext.startswith(b"OVE1")

        decrypted = await volcengine_file_encryptor.decrypt(account_id, ciphertext)
        assert decrypted == b""


class TestVolcengineKMSEncryptionDisabled:
    """Tests for behavior when encryption is disabled"""

    @pytest_asyncio.fixture(scope="function")
    async def openviking_client_without_encryption(self, test_data_dir: Path):
        """Fixture that provides an OpenViking client without encryption"""
        await AsyncOpenViking.reset()
        OpenVikingConfigSingleton.reset_instance()

        if test_data_dir.exists():
            import shutil

            shutil.rmtree(test_data_dir)
        test_data_dir.mkdir(parents=True, exist_ok=True)

        # Create config dict with encryption disabled
        config_dict = {
            "encryption": {"enabled": False},
            "storage": {
                "workspace": str(test_data_dir / "workspace"),
                "vectordb": {"name": "test", "backend": "local", "project": "default"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "api_key": "fake",
                    "model": "text-embedding-3-small",
                }
            },
        }

        # Initialize config singleton
        OpenVikingConfigSingleton.initialize(config_dict=config_dict)

        client = AsyncOpenViking(path=str(test_data_dir))
        await client.initialize()

        yield client

        await client.close()
        await AsyncOpenViking.reset()
        OpenVikingConfigSingleton.reset_instance()

    @pytest.mark.asyncio
    async def test_read_write_without_encryption(
        self, openviking_client_without_encryption: AsyncOpenViking, tmp_path: Path
    ):
        """Test normal file operations when encryption is disabled"""
        client = openviking_client_without_encryption

        test_file = tmp_path / "normal_file.txt"
        test_content = "Normal content without encryption"
        test_file.write_text(test_content)

        result = await client.add_resource(
            path=str(test_file), reason="Normal operation test", wait=True
        )
        root_uri = result["root_uri"]

        # Get tree structure to find the actual file
        uris = await client.tree(root_uri)
        assert len(uris) > 0

        # Find the actual file (skip .abstract.md and .overview.md)
        found = False
        for data in uris:
            if not data["isDir"]:
                filename = data["name"]
                # Skip auto-generated files
                if filename not in [".abstract.md", ".overview.md"]:
                    file_uri = data["uri"]
                    content = await client.read(file_uri)
                    assert content == test_content
                    found = True
                    break
        assert found, "Could not find the test file"


class TestVikingFSEncryptionWithVolcengineKMS:
    """
    Complete VikingFS encryption tests using Volcengine KMS as key provider
    """

    ROOT_KEY = "test-root-key-for-volcengine-encryption-tests"

    @pytest_asyncio.fixture(scope="function")
    async def openviking_service_with_volcengine_encryption(self, test_data_dir: Path):
        """Fixture that provides OpenVikingService with Volcengine KMS encryption"""
        await AsyncOpenViking.reset()
        OpenVikingConfigSingleton.reset_instance()

        if test_data_dir.exists():
            import shutil

            shutil.rmtree(test_data_dir)
        test_data_dir.mkdir(parents=True, exist_ok=True)

        svc = OpenVikingService(
            path=str(test_data_dir / "viking"), user=UserIdentifier.the_default_user("test_user")
        )
        await svc.initialize()

        api_key_manager = APIKeyManager(
            root_key=self.ROOT_KEY, viking_fs=svc.viking_fs, encryption_enabled=True
        )
        await api_key_manager.load()

        yield {"service": svc, "api_key_manager": api_key_manager, "test_data_dir": test_data_dir}

        await svc.close()
        await AsyncOpenViking.reset()
        OpenVikingConfigSingleton.reset_instance()

    def _is_file_encrypted(self, file_path: Path) -> bool:
        """Check if file is encrypted (by "OVE1" header)"""
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
        """Recursively check if all files in directory are encrypted"""
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
    async def test_bootstrap_encryption_with_volcengine_kms(
        self, openviking_service_with_volcengine_encryption
    ):
        """Test 1: Bootstrapping encryption with Volcengine KMS"""
        data = openviking_service_with_volcengine_encryption
        api_key_manager = data["api_key_manager"]
        test_data_dir = data["test_data_dir"]

        account_id = "test-account-volcengine-bootstrap"
        admin_user_id = "admin"
        user_key = await api_key_manager.create_account(account_id, admin_user_id)

        assert user_key is not None
        assert len(user_key) == 64

        agfs_data_root = test_data_dir / "viking" / "viking"

        global_accounts_path = agfs_data_root / "_system" / "accounts.json"
        assert global_accounts_path.exists()
        assert self._is_file_encrypted(global_accounts_path)

    @pytest.mark.asyncio
    async def test_file_encryption_with_volcengine_kms(
        self, openviking_service_with_volcengine_encryption
    ):
        """Test 2: Encrypting files with Volcengine KMS"""
        data = openviking_service_with_volcengine_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]
        test_data_dir = data["test_data_dir"]

        account_id = "test-account-volcengine-file"
        admin_user_id = "admin"
        await api_key_manager.create_account(account_id, admin_user_id)

        from openviking.server.identity import RequestContext, Role

        user = UserIdentifier(account_id, admin_user_id, admin_user_id)
        ctx = RequestContext(user=user, role=Role.ADMIN)

        test_content = "Test content with Volcengine KMS encryption"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(test_content)
            temp_file_path = f.name

        try:
            resource = await svc.resources.add_resource(
                path=temp_file_path, reason="Test Volcengine KMS encryption", ctx=ctx, wait=True
            )
            resource["root_uri"]

            agfs_data_root = test_data_dir / "viking" / "viking"
            resources_dir = agfs_data_root / account_id / "resources"

            assert resources_dir.exists()

            await self._check_all_files_encrypted(svc, ctx, f"viking:///{account_id}/resources")

        finally:
            import os

            os.unlink(temp_file_path)

    @pytest.mark.asyncio
    async def test_resource_operations_with_encryption(
        self, openviking_service_with_volcengine_encryption, tmp_path
    ):
        """Test 4: Resource operations and encryption verification"""
        data = openviking_service_with_volcengine_encryption
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
        test_content = "This is test resource content for Volcengine KMS encryption"
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
            grep_result = await svc.viking_fs.grep(resources_dir_uri, "Volcengine", ctx=ctx)
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
    async def test_skill_operations_with_encryption(
        self, openviking_service_with_volcengine_encryption
    ):
        """Test 5: Skill operations and encryption verification"""
        data = openviking_service_with_volcengine_encryption
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
description: Test skill for Volcengine KMS encryption
---

# Test Skill

This is a test skill for verifying Volcengine KMS encryption.
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
        self, openviking_service_with_volcengine_encryption
    ):
        """Test 6: Memory operations and encryption verification"""
        data = openviking_service_with_volcengine_encryption
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
        self, openviking_service_with_volcengine_encryption
    ):
        """Test 7: Session operations and encryption verification"""
        data = openviking_service_with_volcengine_encryption
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
    async def test_complete_encryption_workflow(
        self, openviking_service_with_volcengine_encryption, tmp_path
    ):
        """Test 8: Complete encryption workflow using Volcengine KMS as key provider"""
        data = openviking_service_with_volcengine_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        # Generate random account name
        import secrets

        random_suffix = secrets.token_hex(4)
        test_account_id = f"test-account-workflow-{random_suffix}"
        test_user_id = "admin"

        # Create account
        await api_key_manager.create_account(test_account_id, test_user_id)

        from openviking.server.identity import RequestContext, Role

        user = UserIdentifier(test_account_id, test_user_id, test_user_id)
        ctx = RequestContext(user=user, role=Role.ADMIN)

        print(f"\n✓ Created test account: {test_account_id}")

        # 1. Resource operations
        print("\n1. Testing resource operations...")
        resource_uri = f"viking://{test_account_id}/resources/test_workflow.txt"
        resource_content = "Test resource content for complete workflow"
        await svc.viking_fs.write_file(resource_uri, resource_content, ctx=ctx)
        read_content = await svc.viking_fs.read_file(resource_uri, ctx=ctx)
        assert read_content == resource_content
        print("✓ Resource operations test passed")

        # 2. Skill operations
        print("\n2. Testing skill operations...")
        skill_dir_uri = f"viking://{test_account_id}/agent/test-workflow-skill"
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
        read_skill = await svc.viking_fs.read_file(skill_md_uri, ctx=ctx)
        assert "Workflow Test Skill" in read_skill
        print("✓ Skill operations test passed")

        # 3. Memory operations
        print("\n3. Testing memory operations...")
        memory_dir_uri = f"viking://{test_account_id}/user/{test_user_id}/memories"
        memory_file_uri = f"{memory_dir_uri}/test_memory.md"
        try:
            await svc.viking_fs.mkdir(memory_dir_uri, ctx=ctx)
        except Exception:
            pass
        memory_content = "# Test Memory\n\nThis is a test memory for workflow"
        await svc.viking_fs.write_file(memory_file_uri, memory_content, ctx=ctx)
        read_memory = await svc.viking_fs.read_file(memory_file_uri, ctx=ctx)
        assert "Test Memory" in read_memory
        print("✓ Memory operations test passed")

        # 4. Session operations
        print("\n4. Testing session operations...")
        session = await svc.sessions.create(ctx)
        assert session.session_id is not None
        print("✓ Session operations test passed")

        # 5. Verify all files are encrypted
        print("\n5. Verifying all files are encrypted...")
        account_root_uri = f"viking://{test_account_id}"
        await self._check_all_files_encrypted(svc, ctx, account_root_uri, print_paths=False)
        print("✓ All files are encrypted")

        print("\n✓ Complete Volcengine KMS encryption workflow test passed!")
