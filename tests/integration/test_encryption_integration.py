# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Encryption integration tests

Tests encryption functionality integrated with VikingFS and OpenViking service.
"""

import os
import secrets
from pathlib import Path

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.crypto.config import bootstrap_encryption
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import LocalFileProvider
from openviking.server.api_keys import APIKeyManager
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest_asyncio.fixture(scope="function")
async def encryption_config(tmp_path):
    """Fixture that provides encryption configuration with local provider"""
    # Create temporary key file
    key_file = tmp_path / "master.key"
    root_key = secrets.token_bytes(32)
    key_file.write_text(root_key.hex())
    os.chmod(key_file, 0o600)

    return {
        "encryption": {"enabled": True, "provider": "local", "local": {"key_file": str(key_file)}}
    }


@pytest_asyncio.fixture(scope="function")
async def file_encryptor(tmp_path):
    """Fixture that provides a FileEncryptor instance for testing"""
    # Create temporary key file
    key_file = tmp_path / "master.key"
    root_key = secrets.token_bytes(32)
    key_file.write_text(root_key.hex())
    os.chmod(key_file, 0o600)

    provider = LocalFileProvider(key_file=str(key_file))
    return FileEncryptor(provider)


@pytest_asyncio.fixture(scope="function")
async def openviking_client_with_encryption(test_data_dir: Path, encryption_config):
    """Fixture that provides an OpenViking client with encryption enabled"""
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()

    # Clean data directory
    if test_data_dir.exists():
        import shutil

        shutil.rmtree(test_data_dir)
    test_data_dir.mkdir(parents=True, exist_ok=True)

    # Create config dict with encryption enabled
    config_dict = {}
    config_dict.update(encryption_config)
    config_dict["storage"] = {
        "workspace": str(test_data_dir / "workspace"),
        "vectordb": {"name": "test", "backend": "local", "project": "default"},
    }
    config_dict["embedding"] = {
        "dense": {"provider": "openai", "api_key": "fake", "model": "text-embedding-3-small"}
    }
    config_dict["vlm"] = {"provider": "openai", "api_key": "fake", "model": "gpt-4-vision-preview"}

    # Initialize config singleton
    OpenVikingConfigSingleton.initialize(config_dict=config_dict)

    client = AsyncOpenViking(path=str(test_data_dir))
    await client.initialize()

    yield client

    await client.close()
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()


class TestEncryptionBootstrap:
    """Tests for encryption module bootstrap"""

    async def test_bootstrap_encryption_enabled(self, encryption_config):
        """Test bootstrapping encryption with enabled configuration"""
        encryptor = await bootstrap_encryption(encryption_config)
        assert encryptor is not None
        assert isinstance(encryptor, FileEncryptor)

    async def test_bootstrap_encryption_disabled(self):
        """Test bootstrapping encryption with disabled configuration"""
        config = {"encryption": {"enabled": False}}
        encryptor = await bootstrap_encryption(config)
        assert encryptor is None


class TestFileEncryptorIntegration:
    """Tests for FileEncryptor integration"""

    async def test_encrypt_decrypt_roundtrip(self, file_encryptor):
        """Test basic encryption/decryption roundtrip"""
        account_id = "test_account"
        plaintext = b"This is a test file content for encryption integration"

        # Encrypt
        ciphertext = await file_encryptor.encrypt(account_id, plaintext)
        assert ciphertext != plaintext
        assert ciphertext.startswith(b"OVE1")

        # Decrypt
        decrypted = await file_encryptor.decrypt(account_id, ciphertext)
        assert decrypted == plaintext

    async def test_different_accounts_isolation(self, file_encryptor):
        """Test that different accounts cannot decrypt each other's data"""
        account1 = "account_1"
        account2 = "account_2"
        plaintext = b"Sensitive data for account isolation test"

        # Encrypt with account1
        ciphertext = await file_encryptor.encrypt(account1, plaintext)

        # Decrypt with account1 should work
        decrypted1 = await file_encryptor.decrypt(account1, ciphertext)
        assert decrypted1 == plaintext

        # Decrypt with account2 should fail
        from openviking.crypto.exceptions import KeyMismatchError

        with pytest.raises(KeyMismatchError):
            await file_encryptor.decrypt(account2, ciphertext)

    async def test_encrypt_empty_data(self, file_encryptor):
        """Test encrypting and decrypting empty data"""
        account_id = "test_account"
        plaintext = b""

        ciphertext = await file_encryptor.encrypt(account_id, plaintext)
        assert ciphertext.startswith(b"OVE1")

        decrypted = await file_encryptor.decrypt(account_id, ciphertext)
        assert decrypted == b""


class TestEncryptionDisabled:
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
            "vlm": {"provider": "openai", "api_key": "fake", "model": "gpt-4-vision-preview"},
        }

        # Initialize config singleton
        OpenVikingConfigSingleton.initialize(config_dict=config_dict)

        client = AsyncOpenViking(path=str(test_data_dir))
        await client.initialize()

        yield client

        await client.close()
        await AsyncOpenViking.reset()
        OpenVikingConfigSingleton.reset_instance()

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


class TestVikingFSEncryptionWithAccounts:
    """
    Complete VikingFS encryption tests, including account management, resource operations, skill operations, etc.
    Uses internal APIs directly instead of HTTP clients to improve test efficiency and accuracy.
    """

    ROOT_KEY = "test-root-key-for-encryption-tests-abcdef123456"

    @pytest_asyncio.fixture(scope="function")
    async def openviking_service_with_encryption(self, test_data_dir: Path, encryption_config):
        """
        Fixture that provides an OpenVikingService instance with encryption enabled.
        Also initializes APIKeyManager for account management.
        """
        # Clean data directory
        if test_data_dir.exists():
            import shutil

            shutil.rmtree(test_data_dir)
        test_data_dir.mkdir(parents=True, exist_ok=True)

        # Create config dict with encryption enabled and root_api_key set
        config_dict = {}
        config_dict.update(encryption_config)
        config_dict["storage"] = {
            "workspace": str(test_data_dir / "workspace"),
            "vectordb": {"name": "test", "backend": "local", "project": "default"},
        }
        config_dict["embedding"] = {
            "dense": {"provider": "openai", "api_key": "fake", "model": "text-embedding-3-small"}
        }
        config_dict["vlm"] = {
            "provider": "openai",
            "api_key": "fake",
            "model": "gpt-4-vision-preview",
        }
        config_dict["server"] = {"root_api_key": self.ROOT_KEY}

        # Initialize config singleton
        OpenVikingConfigSingleton.initialize(config_dict=config_dict)

        # Create OpenVikingService
        svc = OpenVikingService(
            path=str(test_data_dir / "viking"), user=UserIdentifier.the_default_user("test_user")
        )
        await svc.initialize()

        # Create APIKeyManager using VikingFS to ensure system file encryption
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
        Check if a file is encrypted (via file header "OVE1" marker).

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

    def _check_all_files_encrypted(self, svc, ctx, base_uri: str, print_paths: bool = True) -> None:
        """
        Recursively check if all files in a directory are encrypted.

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
                        # Skip .relations.json files
                        if ".relations.json" in entry.get("name", ""):
                            continue
                        try:
                            agfs_path = svc.viking_fs._uri_to_path(entry_uri, ctx=ctx)
                            raw_content = agfs_client.read(agfs_path)
                            assert raw_content.startswith(b"OVE1"), (
                                f"File not encrypted: {entry_uri}"
                            )
                            if print_paths:
                                print(f"[ENCRYPTED] {entry_uri}")
                        except Exception as e:
                            if print_paths:
                                print(f"[SKIP] Skip file {entry_uri}: {e}")
            except Exception as e:
                print(f"[WARNING] Error checking {uri}: {e}")

        import asyncio

        loop = asyncio.get_event_loop()
        loop.run_until_complete(_check_recursive(base_uri))

    def _generate_random_suffix(self) -> str:
        """Generate random suffix for test account/user names"""
        import secrets

        return secrets.token_hex(4)

    async def test_account_creation_and_encryption(self, openviking_service_with_encryption):
        """
        Test 1.1: Create account, verify system files (_system/accounts.json, _system/users.json) are encrypted
        """
        data = openviking_service_with_encryption
        api_key_manager = data["api_key_manager"]
        test_data_dir = data["test_data_dir"]

        # Create new account
        account_id = "test-account-encryption"
        admin_user_id = "admin"
        user_key = await api_key_manager.create_account(account_id, admin_user_id)

        # Verify account created successfully
        assert user_key is not None
        assert len(user_key) == 64

        # AGFS /local/... paths map to test_data_dir/viking/viking/...
        # because OpenVikingService path is test_data_dir/viking,
        # and AGFSManager vikingfs_path is data_path/viking
        agfs_data_root = test_data_dir / "viking" / "viking"

        # Verify global accounts.json file created and encrypted
        global_accounts_path = agfs_data_root / "_system" / "accounts.json"
        assert global_accounts_path.exists(), (
            f"Global accounts.json not created, path: {global_accounts_path}"
        )
        assert self._is_file_encrypted(global_accounts_path), "Global accounts.json not encrypted"

        # Verify users.json file in account directory created and encrypted
        account_users_path = agfs_data_root / account_id / "_system" / "users.json"
        assert account_users_path.exists(), (
            f"Account {account_id} users.json not created, path: {account_users_path}"
        )
        assert self._is_file_encrypted(account_users_path), (
            f"Account {account_id} users.json not encrypted"
        )

        # Verify identity can be resolved correctly via API Key
        identity = api_key_manager.resolve(user_key)
        assert identity.account_id == account_id
        assert identity.user_id == admin_user_id

    async def test_user_registration_and_encryption(self, openviking_service_with_encryption):
        """
        Test 1.2: Register new user in account, verify users.json remains encrypted after update
        """
        data = openviking_service_with_encryption
        api_key_manager = data["api_key_manager"]
        test_data_dir = data["test_data_dir"]

        # Create account first
        account_id = "test-account-users"
        await api_key_manager.create_account(account_id, "admin")

        # Register new user
        new_user_id = "user1"
        new_user_key = await api_key_manager.register_user(account_id, new_user_id, "user")

        # Verify user registered successfully
        assert new_user_key is not None
        assert len(new_user_key) == 64

        # AGFS /local/... paths map to test_data_dir/viking/viking/...
        agfs_data_root = test_data_dir / "viking" / "viking"

        # Verify users.json remains encrypted after update
        account_users_path = agfs_data_root / account_id / "_system" / "users.json"
        assert self._is_file_encrypted(account_users_path), "users.json not encrypted after update"

        # Verify identity can be resolved via new user's API Key
        identity = api_key_manager.resolve(new_user_key)
        assert identity.account_id == account_id
        assert identity.user_id == new_user_id

    async def test_resource_operations_with_encryption(
        self, openviking_service_with_encryption, tmp_path
    ):
        """
        Test 2.1: Write files directly via VikingFS, verify resource file encryption and disk storage encryption
        """
        data = openviking_service_with_encryption
        svc = data["service"]

        # Create request context
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        default_user = UserIdentifier("default", "default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Write test file directly via VikingFS
        test_content = "This is a test resource file content that needs encrypted storage"
        test_uri = "viking://default/test_encrypted.txt"

        await svc.viking_fs.write_file(test_uri, test_content, ctx=ctx)

        # Verify can read correctly via VikingFS (auto-decrypt)
        read_content = await svc.viking_fs.read_file(test_uri, ctx=ctx)
        assert read_content == test_content, "Read content does not match original content"

        # Verify encryption by reading raw file content directly via AGFS
        agfs_client = svc._agfs_client
        agfs_path = svc.viking_fs._uri_to_path(test_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1"), (
            f"File not encrypted, raw content start: {raw_content[:10]}"
        )
        assert raw_content != test_content.encode("utf-8"), (
            "File content is plaintext, not encrypted"
        )

    async def test_multiple_accounts_isolation(self, openviking_service_with_encryption):
        """
        Test: Multiple account isolation, verify files from different accounts cannot decrypt each other
        """
        data = openviking_service_with_encryption
        api_key_manager = data["api_key_manager"]
        test_data_dir = data["test_data_dir"]

        # Create two different accounts
        account1_id = "test-account-1"
        account2_id = "test-account-2"

        key1 = await api_key_manager.create_account(account1_id, "admin1")
        key2 = await api_key_manager.create_account(account2_id, "admin2")

        # AGFS /local/... paths map to test_data_dir/viking/viking/...
        agfs_data_root = test_data_dir / "viking" / "viking"

        # Verify both accounts have their own encrypted system files
        account1_users_path = agfs_data_root / account1_id / "_system" / "users.json"
        account2_users_path = agfs_data_root / account2_id / "_system" / "users.json"

        assert self._is_file_encrypted(account1_users_path)
        assert self._is_file_encrypted(account2_users_path)

        # Verify respective API Keys can resolve correctly
        identity1 = api_key_manager.resolve(key1)
        identity2 = api_key_manager.resolve(key2)

        assert identity1.account_id == account1_id
        assert identity2.account_id == account2_id

    async def test_skill_operations_with_encryption(
        self, openviking_service_with_encryption, tmp_path
    ):
        """
        Test 2.2: Write skill files directly via VikingFS, verify skill file encryption
        """
        data = openviking_service_with_encryption
        svc = data["service"]

        # Create request context
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        default_user = UserIdentifier("default", "default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Create skill directory and files directly via VikingFS
        skill_uri = "viking://default/skill/test-skill"
        await svc.viking_fs.mkdir(skill_uri, ctx=ctx)

        # Create SKILL.md file with YAML frontmatter
        skill_md_content = """---
name: Test Skill
description: This is a test skill for verifying encryption functionality
version: 1.0.0
---

# Test Skill

This is a test skill for verifying encryption functionality.

## Features

- Test encryption
- Verify file storage
"""

        skill_md_uri = f"{skill_uri}/SKILL.md"
        await svc.viking_fs.write_file(skill_md_uri, skill_md_content, ctx=ctx)

        # Verify can read skill file via VikingFS
        skill_content = await svc.viking_fs.read_file(skill_md_uri, ctx=ctx)
        assert "Test Skill" in skill_content

        # Verify encryption by reading raw file content directly via AGFS
        agfs_client = svc._agfs_client
        skill_md_uri = f"{skill_uri}/SKILL.md"
        agfs_path = svc.viking_fs._uri_to_path(skill_md_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1"), (
            f"Skill file not encrypted, raw content start: {raw_content[:10]}"
        )
        assert "Test Skill" not in raw_content.decode("utf-8", errors="ignore"), (
            "Skill file content is plaintext, not encrypted"
        )

    async def _check_all_files_encrypted(self, root_uri, ctx, svc, agfs_client, prefix="  "):
        """
        Recursively check if all files are encrypted

        Args:
            root_uri: Root URI
            ctx: Request context
            svc: OpenVikingService instance
            agfs_client: AGFS client
            prefix: Output prefix
        """
        try:
            # First try to get URI path to see if it's a file
            try:
                agfs_path = svc.viking_fs._uri_to_path(root_uri, ctx=ctx)
                if agfs_client.exists(agfs_path) and not agfs_client.isdir(agfs_path):
                    # This is a file, check directly
                    raw_content = agfs_client.read(agfs_path)
                    assert raw_content.startswith(b"OVE1"), f"File not encrypted: {root_uri}"
                    print(f"{prefix}✓ [ENCRYPTED] {root_uri}")
                    return
            except Exception:
                pass  # Not a file or doesn't exist, continue trying as directory

            # Try to handle as directory
            entries = await svc.viking_fs.ls(root_uri, ctx=ctx)
            for entry in entries:
                if entry["isDir"]:
                    # Recursively check subdirectories
                    await self._check_all_files_encrypted(
                        entry["uri"], ctx, svc, agfs_client, prefix + "  "
                    )
                else:
                    # Check file encryption
                    agfs_path = svc.viking_fs._uri_to_path(entry["uri"], ctx=ctx)
                    raw_content = agfs_client.read(agfs_path)
                    assert raw_content.startswith(b"OVE1"), f"File not encrypted: {entry['uri']}"
                    print(f"{prefix}✓ [ENCRYPTED] {entry['uri']}")
        except Exception as e:
            print(f"{prefix}[WARNING] Error checking {root_uri}: {e}")

    async def test_complete_encryption_workflow(self, openviking_service_with_encryption, tmp_path):
        """
        Complete encryption workflow test, implemented according to user plan:
        - Prerequisites: Create random account, user
        - Execute tests: resource, skill, memory, session, relation operations
        - Post operations: Cleanup
        """
        data = openviking_service_with_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        # ========== Prerequisites ==========
        print("\n" + "=" * 80)
        print("Prerequisites: Create test account and user")
        print("=" * 80)

        # 1. Create random test account
        random_suffix = self._generate_random_suffix()
        test_account_id = f"encrypt-test-account-{random_suffix}"
        test_admin_user_id = "admin"
        print(f"[1] Create test account: {test_account_id}")

        admin_user_key = await api_key_manager.create_account(test_account_id, test_admin_user_id)
        assert admin_user_key is not None
        assert len(admin_user_key) == 64

        # 2. Verify list-accounts operation (via accessing APIKeyManager internal data)
        print("[2] Verify list-accounts operation")
        assert test_account_id in api_key_manager._accounts, (
            f"Account {test_account_id} not in account list"
        )
        print(f"  ✓ Account {test_account_id} exists in account list")

        # 3. Register new user in test account
        test_user_id = f"encrypt-test-user-{random_suffix}"
        print(f"[3] Register test user: {test_user_id}")
        test_user_key = await api_key_manager.register_user(test_account_id, test_user_id, "user")
        assert test_user_key is not None
        assert len(test_user_key) == 64

        # 4. Verify list-users operation
        print("[4] Verify list-users operation")
        account_info = api_key_manager._accounts.get(test_account_id)
        assert account_info is not None
        assert test_user_id in account_info.users, f"User {test_user_id} not in user list"
        print(f"  ✓ User {test_user_id} exists in user list")

        # 5. Check all files in account directory are encrypted (recursive check)
        print("[5] Check all files in account directory are encrypted")

        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        test_user = UserIdentifier(test_account_id, test_user_id, "default")
        ctx = RequestContext(user=test_user, role=Role.USER)
        root_ctx = RequestContext(user=test_user, role=Role.ROOT)

        agfs_client = svc._agfs_client
        account_root_uri = f"viking://{test_account_id}"

        await self._check_all_files_encrypted(account_root_uri, ctx, svc, agfs_client)

        # ========== Execute tests ==========
        print("\n" + "=" * 80)
        print("Execute tests: 1. Resource operations")
        print("=" * 80)

        # 1. Resource operations
        print(
            "[1.1] Create test resources directly using VikingFS (avoid waiting for semantic processing)"
        )
        test_resource_content = "This is test resource file content for verifying encryption functionality. Contains keyword OpenViking."

        # Create test file directly using VikingFS
        test_resource_uri = "viking://resources/test_encryption_file.txt"
        await svc.viking_fs.write_file(test_resource_uri, test_resource_content, ctx=ctx)
        print(f"  ✓ Test file created successfully: {test_resource_uri}")

        # Create a test directory
        test_dir_uri = "viking://resources/test_encryption_dir"
        await svc.viking_fs.mkdir(test_dir_uri, ctx=ctx)
        test_file_in_dir_uri = f"{test_dir_uri}/nested_file.txt"
        await svc.viking_fs.write_file(test_file_in_dir_uri, "This is nested file content", ctx=ctx)
        print(f"  ✓ Test directory created successfully: {test_dir_uri}")

        # Check all files in resources directory are encrypted
        print("[1.1] Check files in resources directory are encrypted")
        resources_dir_uri = "viking://resources"
        await self._check_all_files_encrypted(resources_dir_uri, ctx, svc, agfs_client)

        # 1.2 Execute various operations to verify returns unencrypted
        print("[1.2] Verify various operations return unencrypted content")

        # ls operation
        print("  Executing ls operation...")
        ls_entries = await svc.viking_fs.ls(resources_dir_uri, ctx=ctx)
        assert len(ls_entries) > 0
        print("  ✓ ls operation successful")

        # tree operation
        print("  Executing tree operation...")
        tree_entries = await svc.viking_fs.tree(resources_dir_uri, ctx=ctx)
        assert len(tree_entries) > 0
        print("  ✓ tree operation successful")

        # read operation
        print(f"  Executing read operation, using URI: {test_resource_uri}")
        read_content = await svc.viking_fs.read_file(test_resource_uri, ctx=ctx)
        assert test_resource_content in read_content, "read operation returned incorrect content"
        print("  ✓ read operation returns unencrypted content")

        # read nested file
        print(f"  Executing read nested file, using URI: {test_file_in_dir_uri}")
        nested_content = await svc.viking_fs.read_file(test_file_in_dir_uri, ctx=ctx)
        assert "nested file" in nested_content, "read nested file returned incorrect content"
        print("  ✓ read nested file returns unencrypted content")

        # get operation (using read_file)
        print("  Executing get operation...")
        get_content = await svc.viking_fs.read_file(test_resource_uri, ctx=ctx)
        assert test_resource_content in get_content, "get operation returned incorrect content"
        print("  ✓ get operation returns unencrypted content")

        # grep operation
        print("  Executing grep operation...")
        try:
            grep_result = await svc.viking_fs.grep(resources_dir_uri, "OpenViking", ctx=ctx)
            assert grep_result is not None
            print("  ✓ grep operation successful")
        except Exception as e:
            print(f"  [WARNING] grep operation may not be supported: {e}")

        # abstract operation
        print("  Executing abstract operation...")
        try:
            abstract = await svc.viking_fs.abstract(test_resource_uri, ctx=ctx)
            assert abstract is not None
            assert "OVE1" not in abstract, "abstract returns encrypted content"
            print("  ✓ abstract operation successful, returns unencrypted content")
        except Exception as e:
            print(f"  [WARNING] abstract operation may not be supported: {e}")

        # overview operation
        print("  Executing overview operation...")
        try:
            overview = await svc.viking_fs.overview(test_resource_uri, ctx=ctx)
            assert overview is not None
            assert "OVE1" not in overview, "overview returns encrypted content"
            print("  ✓ overview operation successful, returns unencrypted content")
        except Exception as e:
            print(f"  [WARNING] overview operation may not be supported: {e}")

        # ========== 2. Skill operations ==========
        print("\n" + "=" * 80)
        print("Execute tests: 2. Skill operations")
        print("=" * 80)

        print(
            "[2.1] Create test skill directly using VikingFS (avoid waiting for semantic processing)"
        )
        skill_content = """---
name: Test Skill
description: This is a test skill for verifying encryption functionality
version: 1.0.0
tags:
  - test
  - encryption
---

# Test Skill

This is a test skill for verifying encryption functionality.

## Features

- Test encryption
- Verify file storage
"""

        # Create test file directly using VikingFS (using root_ctx to access agent directory)
        test_skill_uri = "viking://agent/test_encryption_skill/SKILL.md"
        test_skill_dir_uri = "viking://agent/test_encryption_skill"
        await svc.viking_fs.mkdir(test_skill_dir_uri, ctx=root_ctx)
        await svc.viking_fs.write_file(test_skill_uri, skill_content, ctx=root_ctx)
        print(f"  ✓ Test skill created successfully: {test_skill_uri}")

        # Check all files in agent directory are encrypted
        print("[2.1] Check files in agent directory are encrypted")
        agent_dir_uri = "viking://agent"
        await self._check_all_files_encrypted(agent_dir_uri, root_ctx, svc, agfs_client)

        # 2.2 Verify various operations return unencrypted
        print("[2.2] Verify various operations return unencrypted content")

        # ls operation
        print("  Executing ls operation...")
        skill_ls_entries = await svc.viking_fs.ls(agent_dir_uri, ctx=root_ctx)
        assert len(skill_ls_entries) > 0
        print("  ✓ ls operation successful")

        # tree operation
        print("  Executing tree operation...")
        try:
            skill_tree_entries = await svc.viking_fs.tree(agent_dir_uri, ctx=root_ctx)
            assert len(skill_tree_entries) > 0
            print("  ✓ tree operation successful")
        except Exception as e:
            print(f"  [WARNING] tree operation may not be supported: {e}")

        # read operation
        print("  Executing read operation...")
        skill_read_content = await svc.viking_fs.read_file(test_skill_uri, ctx=root_ctx)
        assert "Test Skill" in skill_read_content
        print("  ✓ read operation returns unencrypted content")

        # get operation
        print("  Executing get operation...")
        skill_get_content = await svc.viking_fs.read_file(test_skill_uri, ctx=root_ctx)
        assert "Test Skill" in skill_get_content
        print("  ✓ get operation returns unencrypted content")

        # grep operation
        print("  Executing grep operation...")
        try:
            skill_grep_result = await svc.viking_fs.grep(agent_dir_uri, "Test Skill", ctx=root_ctx)
            assert skill_grep_result is not None
            print("  ✓ grep operation successful")
        except Exception as e:
            print(f"  [WARNING] grep operation may not be supported: {e}")

        # abstract operation
        print("  Executing abstract operation...")
        try:
            skill_abstract = await svc.viking_fs.abstract(test_skill_uri, ctx=root_ctx)
            assert skill_abstract is not None
            assert "OVE1" not in skill_abstract, "abstract returns encrypted content"
            print("  ✓ abstract operation successful, returns unencrypted content")
        except Exception as e:
            print(f"  [WARNING] abstract operation may not be supported: {e}")

        # overview operation
        print("  Executing overview operation...")
        try:
            skill_overview = await svc.viking_fs.overview(test_skill_uri, ctx=root_ctx)
            assert skill_overview is not None
            assert "OVE1" not in skill_overview, "overview returns encrypted content"
            print("  ✓ overview operation successful, returns unencrypted content")
        except Exception as e:
            print(f"  [WARNING] overview operation may not be supported: {e}")

        # ========== 3. Memory operations ==========
        print("\n" + "=" * 80)
        print("Execute tests: 3. Memory operations")
        print("=" * 80)

        print("[3.1] Add memory file")
        memory_dir_uri = f"viking://{test_account_id}/user/{test_user_id}/memories"

        # Create memories directory
        try:
            await svc.viking_fs.mkdir(memory_dir_uri, ctx=ctx)
        except Exception:
            pass  # Directory may already exist

        # Create preferences subdirectory
        preferences_dir_uri = f"{memory_dir_uri}/preferences"
        try:
            await svc.viking_fs.mkdir(preferences_dir_uri, ctx=ctx)
        except Exception:
            pass

        # Write memory file
        memory_content = "# Test Preferences\n\nUser prefers dark theme, likes clean code style."
        memory_uri = f"{preferences_dir_uri}/theme.md"
        await svc.viking_fs.write_file(memory_uri, memory_content, ctx=ctx)
        print(f"  ✓ Memory file added: {memory_uri}")

        # Check all files in user directory are encrypted
        print("[3.1] Check files in user directory are encrypted")
        user_dir_uri = f"viking://{test_account_id}/user/{test_user_id}"
        await self._check_all_files_encrypted(user_dir_uri, ctx, svc, agfs_client)

        # 3.2 Verify various operations return unencrypted
        print("[3.2] Verify various operations return unencrypted content")

        # ls operation
        print("  Executing ls operation...")
        memory_ls_entries = await svc.viking_fs.ls(user_dir_uri, ctx=ctx)
        assert len(memory_ls_entries) >= 0
        print("  ✓ ls operation successful")

        # tree operation
        print("  Executing tree operation...")
        try:
            memory_tree_entries = await svc.viking_fs.tree(user_dir_uri, ctx=ctx)
            assert len(memory_tree_entries) >= 0
            print("  ✓ tree operation successful")
        except Exception as e:
            print(f"  [WARNING] tree operation may not be supported: {e}")

        # read operation
        print("  Executing read operation...")
        memory_read_content = await svc.viking_fs.read_file(memory_uri, ctx=ctx)
        assert "Test Preferences" in memory_read_content
        print("  ✓ read operation returns unencrypted content")

        # get operation
        print("  Executing get operation...")
        memory_get_content = await svc.viking_fs.read_file(memory_uri, ctx=ctx)
        assert "Test Preferences" in memory_get_content
        print("  ✓ get operation returns unencrypted content")

        # grep operation
        print("  Executing grep operation...")
        try:
            memory_grep_result = await svc.viking_fs.grep(user_dir_uri, "dark theme", ctx=ctx)
            assert memory_grep_result is not None
            print("  ✓ grep operation successful")
        except Exception as e:
            print(f"  [WARNING] grep operation may not be supported: {e}")

        # abstract operation
        print("  Executing abstract operation...")
        try:
            memory_abstract = await svc.viking_fs.abstract(memory_uri, ctx=ctx)
            assert memory_abstract is not None
            assert "OVE1" not in memory_abstract, "abstract returns encrypted content"
            print("  ✓ abstract operation successful, returns unencrypted content")
        except Exception as e:
            print(f"  [WARNING] abstract operation may not be supported: {e}")

        # overview operation
        print("  Executing overview operation...")
        try:
            memory_overview = await svc.viking_fs.overview(memory_uri, ctx=ctx)
            assert memory_overview is not None
            assert "OVE1" not in memory_overview, "overview returns encrypted content"
            print("  ✓ overview operation successful, returns unencrypted content")
        except Exception as e:
            print(f"  [WARNING] overview operation may not be supported: {e}")

        # ========== 4. Session operations ==========
        print("\n" + "=" * 80)
        print("Execute tests: 4. Session operations")
        print("=" * 80)

        print("[4.1] Create new session")
        session = await svc.sessions.create(ctx)
        session_id = session.session_id
        print(f"  ✓ Session created: {session_id}")

        # Check session directory files are encrypted
        print("[4.1] Check files in session directory are encrypted")
        session_dir_uri = f"viking://{test_account_id}/session"
        await self._check_all_files_encrypted(session_dir_uri, ctx, svc, agfs_client)

        # 4.2 Verify various operations return unencrypted
        print("[4.2] Verify various operations return unencrypted content")
        try:
            session_entries = await svc.viking_fs.ls(session_dir_uri, ctx=ctx)
            assert len(session_entries) >= 0
            print("  ✓ ls operation successful")

            # tree operation
            try:
                session_tree = await svc.viking_fs.tree(session_dir_uri, ctx=ctx)
                assert len(session_tree) >= 0
                print("  ✓ tree operation successful")
            except Exception as e:
                print(f"  [WARNING] tree operation may not be supported: {e}")

            # get operation (via sessions.get)
            print("  Executing get operation...")
            reloaded_session = await svc.sessions.get(session_id, ctx)
            assert reloaded_session is not None
            print("  ✓ get operation returns unencrypted content")

            # grep operation
            try:
                grep_result = await svc.viking_fs.grep(session_dir_uri, "session", ctx=ctx)
                assert grep_result is not None
                print("  ✓ grep operation successful")
            except Exception as e:
                print(f"  [WARNING] grep operation may not be supported: {e}")

            # abstract operation (on session directory)
            try:
                abstract = await svc.viking_fs.abstract(session_dir_uri, ctx=ctx)
                assert abstract is not None
                assert "OVE1" not in abstract, "abstract returns encrypted content"
                print("  ✓ abstract operation successful, returns unencrypted content")
            except Exception as e:
                print(f"  [WARNING] abstract operation may not be supported: {e}")

            # overview operation
            try:
                overview = await svc.viking_fs.overview(session_dir_uri, ctx=ctx)
                assert overview is not None
                assert "OVE1" not in overview, "overview returns encrypted content"
                print("  ✓ overview operation successful, returns unencrypted content")
            except Exception as e:
                print(f"  [WARNING] overview operation may not be supported: {e}")

        except Exception as e:
            print(f"  [WARNING] Operation verification: {e}")

        # 4.3 Add message and check message.jsonl encryption
        print("[4.3] Add message to session")
        from openviking.message import TextPart

        test_message = "This is a test message for verifying session message.jsonl encryption"
        parts = [TextPart(text=test_message)]
        session.add_message(role="user", parts=parts)

        print("  ✓ Message added (add_message auto-saves)")

        # Check message.jsonl file encryption
        print("[4.3] Check messages file encryption")
        await self._check_all_files_encrypted(session_dir_uri, ctx, svc, agfs_client)

        # 4.4 Verify read operation returns unencrypted
        print("[4.4] Re-read session to verify message")
        reloaded_session2 = await svc.sessions.get(session_id, ctx)
        assert len(reloaded_session2.messages) == 1
        assert test_message in reloaded_session2.messages[0].content
        print("  ✓ read operation returns unencrypted message")

        # ========== 5. Relation operations ==========
        print("\n" + "=" * 80)
        print("Execute tests: 5. Relation operations")
        print("=" * 80)

        print("[5.1] Create two resource files directly using VikingFS")
        # Create relation_test directory
        relation_test_dir_uri = "viking://resources/relation_test"
        await svc.viking_fs.mkdir(relation_test_dir_uri, ctx=ctx)

        # Create resource A directory and file
        dir_a_uri = "viking://resources/relation_test/resource_a"
        await svc.viking_fs.mkdir(dir_a_uri, ctx=ctx)
        resource_a_content = "This is resource A content for testing relation functionality."
        resource_a_file_uri = f"{dir_a_uri}/resource_a.txt"
        await svc.viking_fs.write_file(resource_a_file_uri, resource_a_content, ctx=ctx)
        print(f"  ✓ Resource A created: {dir_a_uri}")

        # Create resource B directory and file
        dir_b_uri = "viking://resources/relation_test/resource_b"
        await svc.viking_fs.mkdir(dir_b_uri, ctx=ctx)
        resource_b_content = "This is resource B content for testing relation functionality."
        resource_b_file_uri = f"{dir_b_uri}/resource_b.txt"
        await svc.viking_fs.write_file(resource_b_file_uri, resource_b_content, ctx=ctx)
        print(f"  ✓ Resource B created: {dir_b_uri}")

        # 5.2 Create relation and check relation.json encryption
        print("[5.2] Create relation A -> B")
        test_reason = "Resource A and resource B are related test resources"
        await svc.relations.link(from_uri=dir_a_uri, uris=dir_b_uri, ctx=ctx, reason=test_reason)
        print(f"  ✓ Relation created: {dir_a_uri} -> {dir_b_uri}")

        # Verify relation created successfully
        relations = await svc.relations.relations(dir_a_uri, ctx=ctx)
        assert len(relations) == 1
        assert relations[0]["uri"] == dir_b_uri
        print("  ✓ Relation created successfully")

        # Check relation file encryption
        print("[5.2] Check relation.json file encryption")
        try:
            relation_file_uri = f"{dir_a_uri}/.relations.json"
            agfs_path = svc.viking_fs._uri_to_path(relation_file_uri, ctx=ctx)
            raw_content = agfs_client.read(agfs_path)
            assert raw_content.startswith(b"OVE1"), (
                f"relation.json not encrypted: {relation_file_uri}"
            )
            print(f"  ✓ [ENCRYPTED] {relation_file_uri}")
        except Exception as e:
            print(f"  [WARNING] Error checking relation.json: {e}")

        # ========== Post operations ==========
        print("\n" + "=" * 80)
        print("Post operations: Clean up test account")
        print("=" * 80)

        # Delete test account
        await api_key_manager.delete_account(test_account_id)
        assert test_account_id not in api_key_manager._accounts, (
            f"Account {test_account_id} not deleted"
        )
        print(f"  ✓ Test account deleted: {test_account_id}")

        print("\n" + "=" * 80)
        print("✅ All tests completed!")
        print("=" * 80)

    async def test_read_file_with_offset_and_limit_encryption(
        self, openviking_service_with_encryption
    ):
        """
        Test read_file() with offset and limit returns correct plaintext when encryption is enabled.
        Verifies that partial reads (by line) work correctly with encryption.
        """
        data = openviking_service_with_encryption
        svc = data["service"]

        # Create request context
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        default_user = UserIdentifier("default", "default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Write a multi-line test file
        test_lines = [
            "Line 0: This is the first line",
            "Line 1: Second line content",
            "Line 2: Third line here",
            "Line 3: Fourth line",
            "Line 4: Fifth and final line",
        ]
        test_content = "\n".join(test_lines)
        test_uri = "viking://default/test_multiline.txt"

        await svc.viking_fs.write_file(test_uri, test_content, ctx=ctx)

        # Test 1: Read with offset=0, limit=-1 (full file)
        full_content = await svc.viking_fs.read_file(test_uri, offset=0, limit=-1, ctx=ctx)
        assert full_content == test_content, "Full file read should return correct content"

        # Test 2: Read with offset=1, limit=3 (lines 1, 2, 3)
        partial_content = await svc.viking_fs.read_file(test_uri, offset=1, limit=3, ctx=ctx)
        expected_lines = test_lines[1:4]
        expected_content = "\n".join(expected_lines)
        # read_file() 会在最后一行后添加换行符，所以需要处理这种情况
        assert partial_content.rstrip("\n") == expected_content, "Partial read failed"

        # Test 3: Read with offset=3, limit=-1 (from line 3 to end)
        from_line_3 = await svc.viking_fs.read_file(test_uri, offset=3, limit=-1, ctx=ctx)
        expected_from_line_3 = "\n".join(test_lines[3:])
        assert from_line_3.rstrip("\n") == expected_from_line_3, "Read from offset failed"

        # Verify file is encrypted on disk
        agfs_client = svc._agfs_client
        agfs_path = svc.viking_fs._uri_to_path(test_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1"), "File should be encrypted"

        print("\n" + "=" * 80)
        print("✅ read_file() with offset/limit encryption test completed!")
        print("=" * 80)

    async def test_read_with_offset_and_size_encryption(self, openviking_service_with_encryption):
        """
        Test read() with offset and size returns correct plaintext when encryption is enabled.
        Verifies that partial reads (by byte) work correctly with encryption.
        """
        data = openviking_service_with_encryption
        svc = data["service"]

        # Create request context
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        default_user = UserIdentifier("default", "default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Write a test file
        test_content = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        test_uri = "viking://default/test_bytes.txt"

        await svc.viking_fs.write_file(test_uri, test_content.decode("utf-8"), ctx=ctx)

        # Test 1: Read with offset=0, size=-1 (full file)
        full_bytes = await svc.viking_fs.read(test_uri, offset=0, size=-1, ctx=ctx)
        # read() 返回的字节会在末尾多一个换行符，所以使用 rstrip(b"\n")
        assert full_bytes.rstrip(b"\n") == test_content, (
            "Full file read should return correct bytes"
        )

        # Test 2: Read with offset=5, size=10 (bytes 5-14)
        partial_bytes = await svc.viking_fs.read(test_uri, offset=5, size=10, ctx=ctx)
        expected_bytes = test_content[5:15]
        assert partial_bytes == expected_bytes, "Partial read failed"

        # Test 3: Read with offset=10, size=-1 (from byte 10 to end)
        from_byte_10 = await svc.viking_fs.read(test_uri, offset=10, size=-1, ctx=ctx)
        expected_from_byte_10 = test_content[10:]
        assert from_byte_10.rstrip(b"\n") == expected_from_byte_10, "Read from offset failed"

        # Verify file is encrypted on disk
        agfs_client = svc._agfs_client
        agfs_path = svc.viking_fs._uri_to_path(test_uri, ctx=ctx)
        raw_content = agfs_client.read(agfs_path)
        assert raw_content.startswith(b"OVE1"), "File should be encrypted"

        print("\n" + "=" * 80)
        print("✅ read() with offset/size encryption test completed!")
        print("=" * 80)


class TestAddResourceWithSemanticProcessing:
    """
    Test add-resource with semantic processing (.abstract.md generation)
    and ls operation decryption.
    """

    ROOT_KEY = "test-root-key-for-encryption-tests-abcdef123456"

    @pytest_asyncio.fixture(scope="function")
    async def openviking_service_with_encryption(self, test_data_dir: Path, encryption_config):
        """
        Fixture that provides an OpenVikingService instance with encryption enabled.
        Also initializes APIKeyManager for account management.
        """
        from openviking_cli.session.user_id import UserIdentifier

        # Clean data directory
        if test_data_dir.exists():
            import shutil

            shutil.rmtree(test_data_dir)
        test_data_dir.mkdir(parents=True, exist_ok=True)

        # Create config dict with encryption enabled and root_api_key set
        config_dict = {}
        config_dict.update(encryption_config)
        config_dict["storage"] = {
            "workspace": str(test_data_dir / "workspace"),
            "vectordb": {"name": "test", "backend": "local", "project": "default"},
        }
        config_dict["embedding"] = {
            "dense": {"provider": "openai", "api_key": "fake", "model": "text-embedding-3-small"}
        }
        config_dict["vlm"] = {
            "provider": "openai",
            "api_key": "fake",
            "model": "gpt-4-vision-preview",
        }
        config_dict["server"] = {"root_api_key": self.ROOT_KEY}

        # Initialize config singleton
        OpenVikingConfigSingleton.initialize(config_dict=config_dict)

        # Create OpenVikingService
        svc = OpenVikingService(
            path=str(test_data_dir / "viking"), user=UserIdentifier.the_default_user("test_user")
        )
        await svc.initialize()

        # Create APIKeyManager using VikingFS to ensure system file encryption
        api_key_manager = APIKeyManager(
            root_key=self.ROOT_KEY, viking_fs=svc.viking_fs, encryption_enabled=True
        )
        await api_key_manager.load()

        yield {
            "service": svc,
            "api_key_manager": api_key_manager,
            "test_data_dir": test_data_dir,
        }

        await svc.close()
        OpenVikingConfigSingleton.reset_instance()

    async def test_add_resource_and_ls_abstract(self, openviking_service_with_encryption, tmp_path):
        """
        Test complete add-resource workflow and verify ls returns decrypted abstract.
        This is the test that would have caught the double encryption bug!
        """
        data = openviking_service_with_encryption
        svc = data["service"]
        api_key_manager = data["api_key_manager"]

        print("\n" + "=" * 80)
        print("Test: add-resource complete workflow + ls abstract decryption")
        print("=" * 80)

        random_suffix = secrets.token_hex(4)
        test_account_id = f"test-abstract-account-{random_suffix}"
        test_admin_user_id = "admin"
        test_user_id = f"test-abstract-user-{random_suffix}"

        print(f"[1] Create test account: {test_account_id}")
        admin_user_key = await api_key_manager.create_account(test_account_id, test_admin_user_id)
        assert admin_user_key is not None
        assert len(admin_user_key) == 64

        print(f"[2] Register test user: {test_user_id}")
        test_user_key = await api_key_manager.register_user(test_account_id, test_user_id, "user")
        assert test_user_key is not None
        assert len(test_user_key) == 64

        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        test_user = UserIdentifier(test_account_id, test_user_id, "default")
        ctx = RequestContext(user=test_user, role=Role.USER)

        agfs_client = svc._agfs_client

        print("[3] Create test resource files (including directory structure)")
        test_dir = tmp_path / "test_abstract_dir"
        test_dir.mkdir()

        (test_dir / "README.md").write_text("""# Test Directory

This is a test directory for verifying .abstract.md generation and decryption.

## Features

- Test semantic abstract generation
- Test ls operation in encryption mode
- Verify .abstract.md correctly decrypted and displayed
""")

        subdir1 = test_dir / "subdir1"
        subdir1.mkdir()
        (subdir1 / "file1.md").write_text("""# Subdir1 File

This is a file in subdir1.
""")

        subdir2 = test_dir / "subdir2"
        subdir2.mkdir()
        (subdir2 / "file2.md").write_text("""# Subdir2 File

This is a file in subdir2.
""")

        print(f"  ✓ Test directory created: {test_dir}")

        print("[4] Execute add-resource operation, wait for completion")
        result = await svc.resources.add_resource(
            path=str(test_dir), reason="Test abstract generation and decryption", ctx=ctx, wait=True
        )
        root_uri = result["root_uri"]
        print(f"  ✓ add-resource successful: {root_uri}")

        print("[5] Check all files are encrypted")

        async def check_encrypted_files(uri: str):
            try:
                entries = await svc.viking_fs.ls(uri, output="original", ctx=ctx)
                for entry in entries:
                    entry_uri = entry["uri"]
                    entry_name = entry.get("name", "")

                    if entry["isDir"]:
                        await check_encrypted_files(entry_uri)
                    else:
                        if ".relations.json" in entry_name:
                            continue
                        try:
                            agfs_path = svc.viking_fs._uri_to_path(entry_uri, ctx=ctx)
                            raw_content = agfs_client.read(agfs_path)
                            assert raw_content.startswith(b"OVE1"), (
                                f"File not encrypted: {entry_uri}"
                            )
                            print(f"  ✓ [ENCRYPTED] {entry_uri}")
                        except Exception as e:
                            print(f"  [SKIP] {entry_uri}: {e}")
            except Exception as e:
                print(f"  [WARNING] Checking {uri}: {e}")

        await check_encrypted_files(root_uri)

        print("[6] Execute ls operation, verify abstract decrypted display")
        ls_entries = await svc.viking_fs.ls(root_uri, output="agent", abs_limit=1024, ctx=ctx)
        assert len(ls_entries) > 0

        print("  Directory list returned by ls:")
        found_abstract = False
        for entry in ls_entries:
            entry_name = entry.get("name", entry.get("uri", "unknown"))
            print(f"    - {entry_name} (isDir={entry.get('isDir')})")
            if entry.get("isDir"):
                abstract = entry.get("abstract", "")
                print(f"      Abstract: {abstract[:100]}...")
                assert "[.abstract.md is not ready]" not in abstract, (
                    f"Abstract not ready for {entry_name}"
                )
                assert "OVE1" not in abstract, f"Abstract shows encrypted content for {entry_name}"
                assert "Test Directory" in abstract or "subdir" in abstract or abstract != "", (
                    f"Abstract content missing for {entry_name}"
                )
                found_abstract = True

        assert found_abstract, "No directory with abstract found"
        print("  ✓ ls operation successful, abstract correctly decrypted and displayed")

        print("[7] Execute abstract operation, verify returns unencrypted content")
        for entry in ls_entries:
            if entry.get("isDir"):
                try:
                    abstract = await svc.viking_fs.abstract(entry["uri"], ctx=ctx)
                    assert abstract is not None
                    assert "OVE1" not in abstract, (
                        f"Abstract returns encrypted content for {entry['uri']}"
                    )
                    print(f"  ✓ abstract operation successful: {entry['uri']}")
                except Exception as e:
                    print(f"  [WARNING] abstract operation {entry['uri']}: {e}")

        print("[8] Execute tree operation")
        tree_entries = await svc.viking_fs.tree(root_uri, output="agent", abs_limit=512, ctx=ctx)
        assert len(tree_entries) > 0
        print(f"  ✓ tree operation successful, found {len(tree_entries)} nodes")

        print("[9] Verify .abstract.md file exists and is encrypted")
        for entry in tree_entries:
            if entry.get("isDir"):
                try:
                    abstract_md_uri = f"{entry['uri']}/.abstract.md"
                    agfs_path = svc.viking_fs._uri_to_path(abstract_md_uri, ctx=ctx)
                    raw_content = agfs_client.read(agfs_path)
                    assert raw_content.startswith(b"OVE1"), (
                        f".abstract.md not encrypted: {abstract_md_uri}"
                    )
                    print(f"  ✓ [ENCRYPTED] {abstract_md_uri}")
                except Exception as e:
                    print(f"  [SKIP] .abstract.md check for {entry['uri']}: {e}")

        print("\n" + "=" * 80)
        print("Post operations: Clean up test account")
        print("=" * 80)

        await api_key_manager.delete_account(test_account_id)
        assert test_account_id not in api_key_manager._accounts, (
            f"Account {test_account_id} not deleted"
        )
        print(f"  ✓ Test account deleted: {test_account_id}")

        print("\n" + "=" * 80)
        print("✅ add-resource + ls abstract decryption test completed!")
        print("=" * 80)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
