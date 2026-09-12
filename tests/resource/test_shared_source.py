# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the shared-upload SOURCE reference and worker materialization."""

import pytest

from openviking.parse.accessors.base import SourceType
from openviking.resource.shared_source import SharedSource, materialize_shared_source


def _valid_payload() -> dict:
    return {
        "temp_file_id": "shared_2026010112abcdef",
        "content_uri": "viking://upload/2026010112/abcdef/content",
        "account_id": "acct-1",
        "original_filename": "repo.zip",
        "file_ext": ".zip",
        "meta": {"resolved_extension": ".zip"},
    }


class TestSharedSourceValidation:
    def test_roundtrip(self) -> None:
        src = SharedSource.from_dict(_valid_payload())
        assert src.temp_file_id == "shared_2026010112abcdef"
        assert src.content_uri == "viking://upload/2026010112/abcdef/content"
        assert SharedSource.from_dict(src.to_dict()) == src

    def test_rejects_non_shared_temp_file_id(self) -> None:
        payload = _valid_payload()
        payload["temp_file_id"] = "upload_plainlocal"
        with pytest.raises(ValueError, match="temp_file_id"):
            SharedSource.from_dict(payload)

    def test_rejects_content_uri_outside_upload_root(self) -> None:
        payload = _valid_payload()
        payload["content_uri"] = "viking://resources/secret/content"
        with pytest.raises(ValueError, match="content_uri"):
            SharedSource.from_dict(payload)

    def test_rejects_missing_account(self) -> None:
        payload = _valid_payload()
        payload["account_id"] = ""
        with pytest.raises(ValueError, match="account_id"):
            SharedSource.from_dict(payload)


class _FakeVikingFS:
    def __init__(self, content: bytes, content_uri: str, account_id: str) -> None:
        self._content = content
        self._content_uri = content_uri
        self._account_id = account_id
        self.read_calls: list[str] = []

    async def exists(self, uri: str, *, ctx) -> bool:
        return uri == self._content_uri

    async def read_file_bytes(self, uri: str, *, ctx) -> bytes:
        self.read_calls.append(uri)
        if uri != self._content_uri:
            raise FileNotFoundError(uri)
        return self._content


class _Ctx:
    account_id = "acct-1"


@pytest.mark.asyncio
class TestMaterializeSharedSource:
    async def test_downloads_content_once_to_local_file(self) -> None:
        src = SharedSource.from_dict(_valid_payload())
        vfs = _FakeVikingFS(b"PK\x03\x04zip-bytes", src.content_uri, "acct-1")

        resource = await materialize_shared_source(src, viking_fs=vfs, ctx=_Ctx())
        try:
            assert resource.source_type == SourceType.LOCAL
            assert resource.path.exists()
            assert resource.path.suffix == ".zip"
            assert resource.path.read_bytes() == b"PK\x03\x04zip-bytes"
            # Worker downloads the shared object exactly once; API never staged it.
            assert vfs.read_calls == [src.content_uri]
        finally:
            resource.cleanup()
        assert not resource.path.exists()

    async def test_missing_content_fails_without_writing_file(self) -> None:
        src = SharedSource.from_dict(_valid_payload())
        vfs = _FakeVikingFS(b"", "viking://upload/other/leaf/content", "acct-1")

        with pytest.raises(ValueError, match="missing"):
            await materialize_shared_source(src, viking_fs=vfs, ctx=_Ctx())
