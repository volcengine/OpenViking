# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for client-side temp uploads when using localhost URLs."""

import json

import pytest

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils.config import OPENVIKING_CLI_CONFIG_ENV


class _FakeHTTPClient:
    def __init__(self):
        self.calls = []

    async def post(self, path, json=None, files=None):
        self.calls.append({"path": path, "json": json, "files": files})
        return object()


@pytest.fixture(autouse=True)
def isolated_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(json.dumps({"url": "http://localhost:1933"}))
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))


@pytest.mark.asyncio
async def test_write_omits_removed_semantic_flags_from_http_payload(tmp_path, monkeypatch):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTPClient()
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"uri": "viking://resources/demo.md"}
    }

    await client.write("viking://resources/demo.md", "updated", wait=True)

    call = fake_http.calls[-1]
    assert call["path"] == "/api/v1/content/write"
    assert call["json"] == {
        "uri": "viking://resources/demo.md",
        "content": "updated",
        "mode": "replace",
        "wait": True,
        "timeout": None,
        "telemetry": False,
    }


@pytest.mark.asyncio
async def test_add_skill_uploads_local_file_even_when_url_is_localhost(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: demo\ndescription: demo\n---\n\n# Demo\n")

    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTPClient()
    client._http = fake_http

    async def fake_upload(_path: str) -> str:
        return "upload_skill.md"

    client._upload_temp_file = fake_upload
    client._handle_response_data = lambda _response: {"result": {"status": "ok"}}

    await client.add_skill(str(skill_file))

    call = fake_http.calls[-1]
    assert call["path"] == "/api/v1/skills"
    assert call["json"]["temp_file_id"] == "upload_skill.md"
    assert "data" not in call["json"]


@pytest.mark.asyncio
async def test_add_resource_uploads_local_file_even_when_url_is_localhost(tmp_path):
    resource_file = tmp_path / "demo.md"
    resource_file.write_text("# Demo\n")

    client = AsyncHTTPClient(url="http://127.0.0.1:1933")
    fake_http = _FakeHTTPClient()
    client._http = fake_http

    async def fake_upload(_path: str) -> str:
        return "upload_resource.md"

    client._upload_temp_file = fake_upload
    client._handle_response_data = lambda _response: {
        "result": {"root_uri": "viking://resources/demo"}
    }

    await client.add_resource(str(resource_file), reason="test")

    call = fake_http.calls[-1]
    assert call["path"] == "/api/v1/resources"
    assert call["json"]["temp_file_id"] == "upload_resource.md"
    assert "path" not in call["json"]


@pytest.mark.asyncio
async def test_import_ovpack_uploads_local_file_even_when_url_is_localhost(tmp_path):
    pack_file = tmp_path / "demo.ovpack"
    pack_file.write_bytes(b"ovpack")

    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTPClient()
    client._http = fake_http

    async def fake_upload(_path: str) -> str:
        return "upload_pack.ovpack"

    client._upload_temp_file = fake_upload
    client._handle_response = lambda _response: {"uri": "viking://resources/imported"}

    await client.import_ovpack(str(pack_file), parent="viking://resources/")

    call = fake_http.calls[-1]
    assert call["path"] == "/api/v1/pack/import"
    assert call["json"]["temp_file_id"] == "upload_pack.ovpack"
    assert "file_path" not in call["json"]


@pytest.mark.asyncio
async def test_import_ovpack_fails_fast_when_local_file_is_missing(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTPClient()
    client._http = fake_http

    missing_path = tmp_path / "missing.ovpack"

    with pytest.raises(FileNotFoundError, match="Local ovpack file not found"):
        await client.import_ovpack(str(missing_path), parent="viking://resources/")

    assert fake_http.calls == []


@pytest.mark.asyncio
async def test_import_ovpack_fails_fast_when_path_is_directory(tmp_path):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTPClient()
    client._http = fake_http

    pack_dir = tmp_path / "pack_dir"
    pack_dir.mkdir()

    with pytest.raises(ValueError, match="is not a file"):
        await client.import_ovpack(str(pack_dir), parent="viking://resources/")

    assert fake_http.calls == []
