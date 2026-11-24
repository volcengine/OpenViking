# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Encryption integration tests

Tests encryption functionality integrated with VikingFS and OpenViking service.
"""

import os
import secrets
from pathlib import Path

import pytest
import pytest_asyncio

from openviking.crypto.config import bootstrap_encryption
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import LocalFileProvider
from openviking.message import TextPart
from openviking.server.api_keys import is_valid_format_key
from openviking.server.identity import RequestContext, Role
from openviking.server.store_assembly import build_api_key_manager
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.server.conftest import _install_fake_embedder, _install_fake_vlm


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
    """Normal resource I/O remains available when encryption is disabled."""

    async def test_read_write_without_encryption(
        self,
        service: OpenVikingService,
        request_context,
        tmp_path: Path,
    ):
        test_file = tmp_path / "normal_file.txt"
        test_content = "Normal content without encryption"
        test_file.write_text(test_content)

        result = await service.resources._execute_resource_ingestion(
            path=str(test_file),
            ctx=request_context,
            defer_post_processing=False,
            reason="Normal operation test",
            build_index=False,
        )
        entries = (await service.fs.ls(result["root_uri"], ctx=request_context)).entries
        contents = [
            await service.fs.read(data["uri"], ctx=request_context)
            for data in entries
            if not data["isDir"] and data["name"] not in {".abstract.md", ".overview.md"}
        ]
        assert test_content in contents


@pytest_asyncio.fixture
async def openviking_service_with_encryption(tmp_path, encryption_config, monkeypatch):
    _install_fake_embedder(monkeypatch)
    _install_fake_vlm(monkeypatch)
    root_key = "test-root-key-for-encryption-tests-abcdef123456"
    config = {
        **encryption_config,
        "storage": {
            "workspace": str(tmp_path / "workspace"),
            "vectordb": {"name": "test", "backend": "local", "project": "default"},
        },
        "embedding": {
            "dense": {"provider": "openai", "api_key": "fake", "model": "text-embedding-3-small"}
        },
        "vlm": {"provider": "openai", "api_key": "fake", "model": "gpt-4-vision-preview"},
        "server": {"root_api_key": root_key},
    }
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(config_dict=config)
    service = OpenVikingService(path=str(tmp_path / "viking"))
    manager = None
    try:
        await service.initialize()
        manager = build_api_key_manager(root_key=root_key, viking_fs=service.viking_fs)
        await manager.load()
        yield {"service": service, "api_key_manager": manager, "test_data_dir": tmp_path}
    finally:
        if manager is not None:
            await manager.close()
        await service.close()
        OpenVikingConfigSingleton.reset_instance()


def _backend_file_bytes(service, ctx, data_dir: Path, uri: str) -> bytes:
    path = service.viking_fs._uri_to_path(uri, ctx=ctx).removeprefix("/local/")
    return (data_dir / "viking" / "viking" / path).read_bytes()


class TestVikingFSEncryptionWithAccounts:
    """
    Complete VikingFS encryption tests, including account management, resource operations, skill operations, etc.
    Uses internal APIs directly instead of HTTP clients to improve test efficiency and accuracy.
    """

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
        assert is_valid_format_key(user_key)

        # RAGFS /local/... paths map to test_data_dir/viking/viking/...
        # because OpenVikingService path is test_data_dir/viking,
        # and RAGFS vikingfs_path is data_path/viking
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
        identity = await api_key_manager.resolve_identity(user_key)
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
        assert is_valid_format_key(new_user_key)

        # AGFS /local/... paths map to test_data_dir/viking/viking/...
        agfs_data_root = test_data_dir / "viking" / "viking"

        # Verify users.json remains encrypted after update
        account_users_path = agfs_data_root / account_id / "_system" / "users.json"
        assert self._is_file_encrypted(account_users_path), "users.json not encrypted after update"

        # Verify identity can be resolved via new user's API Key
        identity = await api_key_manager.resolve_identity(new_user_key)
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
        default_user = UserIdentifier("default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Write test file directly via VikingFS
        test_content = "This is a test resource file content that needs encrypted storage"
        test_uri = "viking://resources/test_encrypted.txt"

        await svc.viking_fs.write_file(test_uri, test_content, ctx=ctx)

        # Verify can read correctly via VikingFS (auto-decrypt)
        read_content = await svc.viking_fs.read_file(test_uri, ctx=ctx)
        assert read_content == test_content, "Read content does not match original content"

        # Verify encryption by reading backend file content directly
        raw_content = _backend_file_bytes(svc, ctx, data["test_data_dir"], test_uri)
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
        identity1 = await api_key_manager.resolve_identity(key1)
        identity2 = await api_key_manager.resolve_identity(key2)

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
        default_user = UserIdentifier("default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Create skill directory and files directly via VikingFS
        skill_uri = "viking://user/default/skills/test-skill"
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
        skill_md_uri = f"{skill_uri}/SKILL.md"
        raw_content = _backend_file_bytes(svc, ctx, data["test_data_dir"], skill_md_uri)
        assert raw_content.startswith(b"OVE1"), (
            f"Skill file not encrypted, raw content start: {raw_content[:10]}"
        )
        assert "Test Skill" not in raw_content.decode("utf-8", errors="ignore"), (
            "Skill file content is plaintext, not encrypted"
        )

    async def test_complete_encryption_workflow(self, openviking_service_with_encryption):
        data = openviking_service_with_encryption
        service = data["service"]
        manager = data["api_key_manager"]
        account_id = "encrypted-account"
        await manager.create_account(account_id, "admin")
        key = await manager.register_user(account_id, "alice", "user")
        identity = await manager.resolve_identity(key)
        assert (identity.account_id, identity.user_id) == (account_id, "alice")
        ctx = RequestContext(user=UserIdentifier(account_id, "alice"), role=Role.USER)

        session = await service.sessions.create(ctx)
        message = "A private message inside the encrypted workspace."
        await session.add_message_async("user", [TextPart(text=message)])
        loaded = await service.sessions.get(session.session_id, ctx)
        assert [item.parts[0].text for item in loaded.messages] == [message]

        files = {
            "viking://resources/encrypted-note.md": "Shared file with an encrypted body.",
            "viking://user/alice/memories/preferences/preference.md": "Use the offline model.",
            "viking://user/alice/skills/demo/SKILL.md": "# A private skill",
        }
        for uri, content in files.items():
            await service.viking_fs.write_file(uri, content, ctx=ctx)
            assert await service.viking_fs.read_file(uri, ctx=ctx) == content
            raw = _backend_file_bytes(service, ctx, data["test_data_dir"], uri)
            assert raw.startswith(b"OVE1")
            assert content.encode() not in raw

        messages_uri = f"{session.uri}/messages.jsonl"
        raw_messages = _backend_file_bytes(service, ctx, data["test_data_dir"], messages_uri)
        assert raw_messages.startswith(b"OVE1")
        assert message.encode() not in raw_messages

        await manager.delete_account(account_id)
        assert await manager.get_account(account_id) is None

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
        default_user = UserIdentifier("default", "default")
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
        test_uri = "viking://resources/test_multiline.txt"

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
        raw_content = _backend_file_bytes(svc, ctx, data["test_data_dir"], test_uri)
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
        default_user = UserIdentifier("default", "default")
        ctx = RequestContext(user=default_user, role=Role.ROOT)

        # Write a test file
        test_content = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        test_uri = "viking://resources/test_bytes.txt"

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
        raw_content = _backend_file_bytes(svc, ctx, data["test_data_dir"], test_uri)
        assert raw_content.startswith(b"OVE1"), "File should be encrypted"

        print("\n" + "=" * 80)
        print("✅ read() with offset/size encryption test completed!")
        print("=" * 80)


class TestAddResourceWithSemanticProcessing:
    """
    Test add-resource with semantic processing (.abstract.md generation)
    and ls operation decryption.
    """

    async def test_add_resource_and_ls_abstract(self, openviking_service_with_encryption, tmp_path):
        data = openviking_service_with_encryption
        service = data["service"]
        manager = data["api_key_manager"]
        account_id = "encrypted-import"
        await manager.create_account(account_id, "alice")
        ctx = RequestContext(user=UserIdentifier(account_id, "alice"), role=Role.ADMIN)

        source = tmp_path / "import-source"
        (source / "topic").mkdir(parents=True)
        (source / "README.md").write_text("# Directory of guides\n")
        body = "# Guide\n\nKeep the encrypted guide readable.\n"
        (source / "topic" / "guide.md").write_text(body)
        root = "viking://resources/encrypted-guide"
        result = await service.resources.add_resource(
            path=str(source), to=root, ctx=ctx, wait=True, args={"parse_mode": "no_split"}
        )
        assert result["status"] == "success", result
        assert result["root_uri"] == root

        directory = f"{root}/topic"
        document = f"{directory}/guide.md"
        assert await service.fs.read(document, ctx=ctx) == body
        entries = (await service.fs.ls(root, ctx=ctx, include_abstract=True)).entries
        [entry] = [item for item in entries if item["uri"] == directory]
        assert entry["isDir"]
        abstract = await service.fs.abstract(directory, ctx=ctx)
        assert abstract
        assert entry["abstract"] == abstract

        for uri, plaintext in [(document, body), (f"{directory}/.abstract.md", abstract)]:
            raw = _backend_file_bytes(service, ctx, data["test_data_dir"], uri)
            assert raw.startswith(b"OVE1")
            assert plaintext.encode() not in raw


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
