# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for VikingFS encrypted helper paths."""

import asyncio
import os
import secrets
from pathlib import Path

import pytest

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import KeyMismatchError
from openviking.crypto.providers import LocalFileProvider
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


class _FakeAGFS:
    def __init__(self):
        self.dirs = {"/", "/local"}
        self.files = {}

    def mkdir(self, path):
        if path in self.files:
            raise FileExistsError(path)
        if path in self.dirs:
            raise FileExistsError(path)
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.dirs.add(path)
        return path

    def write(self, path, data):
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.files[path] = bytes(data)
        return path

    def read(self, path, offset=0, size=-1):
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        return data[offset:] if size == -1 else data[offset : offset + size]

    def stat(self, path):
        if path in self.dirs:
            return {"isDir": True, "size": 0}
        if path in self.files:
            return {"isDir": False, "size": len(self.files[path])}
        raise FileNotFoundError(path)


def _ctx(account_id: str, user_id: str, role: Role = Role.USER) -> RequestContext:
    return RequestContext(user=UserIdentifier(account_id, user_id, user_id), role=role)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def encryptor(tmp_path: Path) -> FileEncryptor:
    key_file = tmp_path / "master.key"
    key_file.write_text(secrets.token_bytes(32).hex())
    os.chmod(key_file, 0o600)
    return FileEncryptor(LocalFileProvider(key_file=str(key_file)))


@pytest.fixture
def encrypted_fs(encryptor: FileEncryptor) -> VikingFS:
    return VikingFS(agfs=_FakeAGFS(), encryptor=encryptor)


def test_bound_context_helper_paths_encrypt_and_decrypt_consistently(encrypted_fs: VikingFS):
    ctx = _ctx("acct-a", "alice")
    file_uri = "viking://resources/docs/guide.md"
    dir_uri = "viking://resources/docs"

    with encrypted_fs.bind_request_context(ctx):
        _run(encrypted_fs.write_file(file_uri, "hello"))
        _run(encrypted_fs.append_file(file_uri, "\nworld"))
        _run(encrypted_fs.write_file(f"{dir_uri}/.abstract.md", "abstract text"))
        _run(encrypted_fs.write_file(f"{dir_uri}/.overview.md", "overview text"))
        _run(encrypted_fs.link(dir_uri, file_uri, reason="primary document"))

        assert _run(encrypted_fs.read_file(file_uri)) == "hello\nworld"
        assert _run(encrypted_fs.read_file_bytes(file_uri)) == b"hello\nworld"
        assert _run(encrypted_fs.abstract(dir_uri)) == "abstract text"
        assert _run(encrypted_fs.overview(dir_uri)) == "overview text"
        assert _run(encrypted_fs.relations(dir_uri)) == [
            {"uri": file_uri, "reason": "primary document"}
        ]

    raw_file = encrypted_fs.agfs.read("/local/acct-a/resources/docs/guide.md")
    raw_abstract = encrypted_fs.agfs.read("/local/acct-a/resources/docs/.abstract.md")
    raw_relations = encrypted_fs.agfs.read("/local/acct-a/resources/docs/.relations.json")
    assert raw_file.startswith(b"OVE1")
    assert raw_abstract.startswith(b"OVE1")
    assert raw_relations.startswith(b"OVE1")


def test_raw_helper_surfaces_wrong_account_decrypt_failure(encrypted_fs: VikingFS):
    owner_ctx = _ctx("acct-a", "alice")
    other_ctx = _ctx("acct-b", "bob")
    uri = "viking://resources/private.txt"

    _run(encrypted_fs.write_file(uri, "secret", ctx=owner_ctx))
    path = encrypted_fs._uri_to_path(uri, ctx=owner_ctx)

    with pytest.raises(KeyMismatchError):
        _run(encrypted_fs._read_path_bytes(path, ctx=other_ctx, decrypt=True, require_exists=True))


@pytest.mark.parametrize("method_name,arg", [("write_file", "nope"), ("write_file_bytes", b"nope")])
def test_non_root_cannot_mutate_temp_root_via_helper_writes(
    encrypted_fs: VikingFS, method_name: str, arg
):
    ctx = _ctx("acct-a", "alice", role=Role.USER)

    with pytest.raises(PermissionDeniedError, match="Temp root is read-only"):
        _run(getattr(encrypted_fs, method_name)("viking://temp", arg, ctx=ctx))


def test_non_root_cannot_append_temp_root_via_helper_write(encrypted_fs: VikingFS):
    ctx = _ctx("acct-a", "alice", role=Role.USER)

    with pytest.raises(PermissionDeniedError, match="Temp root is read-only"):
        _run(encrypted_fs.append_file("viking://temp", "nope", ctx=ctx))
