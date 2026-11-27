# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Encryption integration tests

Tests encryption functionality integrated with VikingFS and OpenViking service.
"""

import secrets
from pathlib import Path

import pytest
import pytest_asyncio

from openviking.crypto.config import bootstrap_encryption
from openviking.crypto.exceptions import KeyMismatchError
from openviking.message import TextPart
from openviking.server.api_keys import is_valid_format_key
from openviking.server.identity import RequestContext, Role
from openviking.server.store_assembly import build_api_key_manager
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.integration.conftest import (
    VAULT_ADDR,
    VAULT_TOKEN,
    VOLCENGINE_ACCESS_KEY,
    VOLCENGINE_KMS_KEY_ID,
    VOLCENGINE_KMS_REGION,
    VOLCENGINE_SECRET_KEY,
    requires_vault,
    requires_volcengine_kms,
)
from tests.server.conftest import _install_fake_embedder, _install_fake_vlm


@pytest.fixture
def encryption_provider():
    return "local"


@pytest.fixture
def encryption_config(tmp_path, encryption_provider):
    if encryption_provider == "disabled":
        return {"encryption": {"enabled": False}}
    if encryption_provider == "vault":
        options = {"address": VAULT_ADDR, "token": VAULT_TOKEN}
    elif encryption_provider == "volcengine_kms":
        options = {
            "region": VOLCENGINE_KMS_REGION,
            "access_key": VOLCENGINE_ACCESS_KEY,
            "secret_key": VOLCENGINE_SECRET_KEY,
            "key_id": VOLCENGINE_KMS_KEY_ID,
            "key_file": str(tmp_path / "kms-root-key.enc"),
        }
    else:
        key_file = tmp_path / "master.key"
        key_file.write_text(secrets.token_hex(32))
        key_file.chmod(0o600)
        options = {"key_file": str(key_file)}
    return {
        "encryption": {
            "enabled": True,
            "provider": encryption_provider,
            encryption_provider: options,
        }
    }


@pytest_asyncio.fixture
async def configured_service(tmp_path, encryption_config, monkeypatch):
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
        await service.recover()
        await service.start_background()
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
    @pytest.mark.parametrize(
        "encryption_provider,marker",
        [
            ("local", b"\x01"),
            pytest.param("vault", b"\x02", marks=[requires_vault, pytest.mark.integration]),
            pytest.param(
                "volcengine_kms",
                b"\x03",
                marks=[requires_volcengine_kms, pytest.mark.integration],
            ),
        ],
    )
    async def test_complete_encryption_workflow(
        self, configured_service, encryption_config, marker
    ):
        data = configured_service
        service = data["service"]
        manager = data["api_key_manager"]
        account_id = "encrypted-account"
        admin_key = await manager.create_account(account_id, "admin")
        admin = await manager.resolve_identity(admin_key)
        assert (admin.account_id, admin.user_id, admin.role) == (account_id, "admin", Role.ADMIN)
        other_account = "other-account"
        other_key = await manager.create_account(other_account, "alice")
        other = await manager.resolve_identity(other_key)
        assert (other.account_id, other.user_id) == (other_account, "alice")
        key = await manager.register_user(account_id, "alice", "user")
        assert is_valid_format_key(key)
        identity = await manager.resolve_identity(key)
        assert (identity.account_id, identity.user_id) == (account_id, "alice")
        ctx = RequestContext(user=UserIdentifier(account_id, "alice"), role=Role.USER)

        header = b"OVE1\x01" + marker
        backend = data["test_data_dir"] / "viking" / "viking"
        assert (backend / "_system" / "accounts.json").read_bytes().startswith(header)
        for account in (account_id, other_account):
            assert (backend / account / "_system" / "users.json").read_bytes().startswith(header)

        session = await service.sessions.create(ctx)
        message = "A private message inside the encrypted workspace."
        await session.add_message_async("user", [TextPart(text=message)])
        loaded = await service.sessions.get(session.session_id, ctx)
        assert [item.parts[0].text for item in loaded.messages] == [message]

        reopened = await bootstrap_encryption(encryption_config)
        assert reopened is not None
        resource = "viking://resources/encrypted-note.md"
        files = {
            resource: "Shared file with an encrypted body.",
            "viking://resources/empty.md": "",
            "viking://user/alice/memories/preferences/preference.md": "Use the offline model.",
            "viking://user/alice/skills/demo/SKILL.md": "# A private skill",
        }
        for uri, content in files.items():
            await service.viking_fs.write_file(uri, content, ctx=ctx)
            assert await service.viking_fs.read_file(uri, ctx=ctx) == content
            raw = _backend_file_bytes(service, ctx, data["test_data_dir"], uri)
            assert raw.startswith(header)
            assert raw != content.encode()
            assert await reopened.decrypt(account_id, raw) == content.encode()

        matches = await service.viking_fs.grep(
            "viking://resources", "Shared file", ctx=ctx, force_fs=True
        )
        assert {match["uri"] for match in matches["matches"]} == {resource}

        messages_uri = f"{session.uri}/messages.jsonl"
        raw_messages = _backend_file_bytes(service, ctx, data["test_data_dir"], messages_uri)
        assert raw_messages.startswith(header)
        assert message.encode() not in raw_messages
        with pytest.raises(KeyMismatchError):
            await reopened.decrypt(other_account, raw_messages)

        await manager.delete_account(account_id)
        assert await manager.get_account(account_id) is None

    async def test_encrypted_file_reads_at_line_and_byte_offsets(self, configured_service):
        service = configured_service["service"]
        ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)
        uri = "viking://resources/multiline.txt"
        lines = ["First line", "Second line", "Third line", "Fourth line", "Fifth line"]
        content = "\n".join(lines)
        await service.viking_fs.write_file(uri, content, ctx=ctx)
        assert _backend_file_bytes(
            service, ctx, configured_service["test_data_dir"], uri
        ).startswith(b"OVE1")

        for offset, limit, expected in ((0, -1, lines), (1, 3, lines[1:4]), (3, -1, lines[3:])):
            text = await service.viking_fs.read_file(uri, offset=offset, limit=limit, ctx=ctx)
            assert text.rstrip("\n") == "\n".join(expected)

        plaintext = content.encode()
        for offset, size, expected_bytes in (
            (0, -1, plaintext),
            (5, 10, plaintext[5:15]),
            (10, -1, plaintext[10:]),
        ):
            data = await service.viking_fs.read(uri, offset=offset, size=size, ctx=ctx)
            assert data == expected_bytes


class TestAddResourceWithSemanticProcessing:
    """
    Test add-resource with semantic processing (.abstract.md generation)
    and ls operation decryption.
    """

    @pytest.mark.parametrize("encryption_provider", ["local", "disabled"])
    async def test_add_resource_and_ls_abstract(
        self, configured_service, tmp_path, encryption_config
    ):
        data = configured_service
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
            if encryption_config["encryption"]["enabled"]:
                assert raw.startswith(b"OVE1")
                assert plaintext.encode() not in raw
            else:
                assert not raw.startswith(b"OVE1")
                if uri == document:
                    assert raw == plaintext.encode()
                else:
                    assert plaintext in raw.decode()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
