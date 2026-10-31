# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import errno
import json
import tempfile
import zipfile
from pathlib import Path

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient
from openviking_sdk.uploads import zip_directory


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("method", ["add_skill", "update_skill"])
@pytest.mark.parametrize("source_kind", ["inline", "file", "directory"])
async def test_skill_inputs_preserve_inline_content_and_local_uploads(
    tmp_path, sync, method, source_kind
):
    content = "---\nname: demo\ndescription: Inline skill\n---\n" + "Instructions.\n" * 100
    source = content
    if source_kind != "inline":
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        source = str(skill_file if source_kind == "file" else tmp_path)
    requests = []

    def respond(request):
        requests.append(request)
        result = (
            {"temp_file_id": "upload_skill"}
            if request.url.path.endswith("/temp_upload")
            else {"name": "demo"}
        )
        return httpx.Response(200, json={"status": "ok", "result": result})

    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if isinstance(client, SyncHTTPClient) else client
    async with httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(respond)
    ) as http:
        async_client._http = http
        args = ("demo", source) if method == "update_skill" else (source,)
        result = getattr(client, method)(*args)
        if not sync:
            result = await result
    assert result == {"name": "demo"}
    assert requests[-1].method == ("PUT" if method == "update_skill" else "POST")
    assert requests[-1].url.path == (
        "/api/v1/skills/demo" if method == "update_skill" else "/api/v1/skills"
    )
    payload = json.loads(requests[-1].content)
    if source_kind == "inline":
        assert len(requests) == 1
        assert payload == {"data": content, "wait": False}
    else:
        assert len(requests) == 2
        assert requests[0].url.path == "/api/v1/resources/temp_upload"
        assert payload == {"temp_file_id": "upload_skill", "wait": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("method", ["add_skill", "update_skill"])
async def test_skill_path_permission_errors_propagate(monkeypatch, sync, method):
    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    error = PermissionError(errno.EACCES, "Permission denied")

    def deny_access(_path):
        raise error

    monkeypatch.setattr(Path, "exists", deny_access)
    args = (
        ("demo", "/unreadable/SKILL.md") if method == "update_skill" else ("/unreadable/SKILL.md",)
    )
    with pytest.raises(PermissionError) as raised:
        result = getattr(client, method)(*args)
        if not sync:
            await result
    assert raised.value is error


@pytest.fixture
def interrupted_archive(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_text("first", encoding="utf-8")
    (source / "second.txt").write_text("second", encoding="utf-8")
    archives = tmp_path / "archives"
    archives.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(archives))
    original_write = zipfile.ZipFile.write
    error = FileNotFoundError("source disappeared during packing")
    writes = []

    def fail_second_write(self, filename, *args, **kwargs):
        writes.append(filename)
        if len(writes) == 2:
            raise error
        return original_write(self, filename, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_second_write)
    return source, archives, error


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("method", ["add_resource", "add_skill", "update_skill"])
async def test_directory_import_removes_partial_zip(interrupted_archive, sync, method):
    source, archives, error = interrupted_archive
    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    args = ("demo", str(source)) if method == "update_skill" else (str(source),)
    with pytest.raises(FileNotFoundError) as raised:
        if sync:
            getattr(client, method)(*args)
        else:
            await getattr(client, method)(*args)
    assert raised.value is error
    assert list(archives.iterdir()) == []
    assert (source / "first.txt").read_text(encoding="utf-8") == "first"
    assert (source / "second.txt").read_text(encoding="utf-8") == "second"


def test_zip_helper_removes_partial_zip(interrupted_archive):
    source, archives, error = interrupted_archive
    with pytest.raises(FileNotFoundError) as raised:
        zip_directory(str(source))
    assert raised.value is error
    assert list(archives.iterdir()) == []


class _FakeHTTPClient:
    def __init__(self):
        self.calls = []

    async def post(self, path, json=None, files=None, data=None):
        self.calls.append({"path": path, "json": json, "files": files, "data": data})
        return object()


def test_zip_directory_creates_forward_slash_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        root_dir = tmpdir / "test_project"
        root_dir.mkdir()
        (root_dir / "file1.txt").write_text("content1")
        (root_dir / "subdir").mkdir()
        (root_dir / "subdir" / "file2.txt").write_text("content2")

        client = AsyncHTTPClient(url="http://localhost:1933")
        zip_path = client._zip_directory(str(root_dir))

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert "file1.txt" in names
                assert "subdir/file2.txt" in names
                assert all("\\" not in name for name in names)
        finally:
            Path(zip_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_temp_file_forwards_upload_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        upload_file = Path(tmpdir) / "demo.md"
        upload_file.write_text("# Demo\n")

        client = AsyncHTTPClient(
            url="http://localhost:1933",
            upload_mode="shared",
        )
        fake_http = _FakeHTTPClient()
        client._http = fake_http
        client._handle_response = lambda _response: {"temp_file_id": "shared_abc"}

        temp_file_id = await client._upload_temp_file(str(upload_file))

        assert temp_file_id == "shared_abc"
        call = fake_http.calls[-1]
        assert call["path"] == "/api/v1/resources/temp_upload"
        assert call["data"] == {"upload_mode": "shared"}
