# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import io
import json
import tempfile
import zipfile
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient


def test_directory_import_removes_partial_zip(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_text("first", encoding="utf-8")
    (source / "second.txt").write_text("second", encoding="utf-8")
    archives = tmp_path / "archives"
    archives.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(archives))
    original_write = zipfile.ZipFile.write

    def fail_after_first_entry(archive, filename, *args, **kwargs):
        if archive.namelist():
            raise FileNotFoundError("source disappeared during packing")
        return original_write(archive, filename, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_after_first_entry)
    client = SyncHTTPClient(url="http://localhost:1933")

    with pytest.raises(FileNotFoundError, match="source disappeared during packing"):
        client.add_resource(str(source))

    assert list(archives.iterdir()) == []
    assert (source / "first.txt").read_text(encoding="utf-8") == "first"
    assert (source / "second.txt").read_text(encoding="utf-8") == "second"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_links", [False, True], ids=["regular-files", "omit-links"])
async def test_zip_directory_creates_forward_slash_paths(
    tmp_path, monkeypatch, sdk_transport, with_links
):
    source = tmp_path / "project"
    nested = source / "subdir"
    nested.mkdir(parents=True)
    (source / "same.txt").write_bytes(b"root")
    (nested / "same.txt").write_bytes(b"nested")
    if with_links:
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"outside")
        try:
            (source / "file-link").symlink_to(outside)
            (source / "inside-link").symlink_to(source / "same.txt")
            (source / "dir-link").symlink_to(tmp_path, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlinks are not available in this environment: {exc}")
    archives = tmp_path / "archives"
    archives.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(archives))
    result = {"root_uri": "viking://resources/project"}
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/resources/temp_upload":
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
                + request.content
            )
            (file_part,) = message.iter_parts()
            zip_data = file_part.get_payload(decode=True)
            assert isinstance(zip_data, bytes)
            with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
                assert sorted(archive.namelist()) == ["same.txt", "subdir/same.txt"]
                assert archive.read("same.txt") == b"root"
                assert archive.read("subdir/same.txt") == b"nested"
            return httpx.Response(
                200, json={"status": "ok", "result": {"temp_file_id": "upload-1"}}
            )
        assert json.loads(request.content) == {
            "source_name": "project",
            "temp_file_id": "upload-1",
            "wait": False,
        }
        return httpx.Response(200, json={"status": "ok", "result": result})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        assert await client.add_resource(str(source)) == result
    finally:
        await client.close()

    assert paths == ["/api/v1/resources/temp_upload", "/api/v1/resources"]
    assert list(archives.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upload_mode", "expected_mode"),
    [(None, b"shared"), ("local", b"local")],
    ids=["from-config", "explicit-override"],
)
async def test_upload_temp_file_forwards_upload_mode(
    tmp_path, monkeypatch, sdk_transport, upload_mode, expected_mode
):
    config = tmp_path / "ovcli.conf"
    config.write_text(
        json.dumps(
            {
                "url": "http://gateway.example",
                "gateway_token": "gateway-token",
                "upload": {
                    "mode": "shared",
                    "ignore_dirs": ".cache",
                    "include": "*.md",
                    "exclude": "*.tmp",
                },
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config))
    source = tmp_path / "demo.md"
    source.write_bytes(b"# Demo\n")
    requests = []

    def handle(request):
        requests.append(request)
        token = request.headers.get("X-Gateway-Token")
        if token is None:
            return httpx.Response(401, headers={"X-VikingBot-Gateway": "true"})
        if request.url.path == "/api/v1/resources/temp_upload":
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
                + request.content
            )
            file_part, mode_part = sorted(
                message.iter_parts(),
                key=lambda part: part.get_filename() is None,
            )
            assert file_part.get_filename() == "demo.md"
            assert file_part.get_payload(decode=True) == b"# Demo\n"
            assert mode_part.get_param("name", header="content-disposition") == "upload_mode"
            assert mode_part.get_payload(decode=True) == expected_mode
            return httpx.Response(
                200, json={"status": "ok", "result": {"temp_file_id": "upload-1"}}
            )
        assert json.loads(request.content)["temp_file_id"] == "upload-1"
        return httpx.Response(
            200, json={"status": "ok", "result": {"root_uri": "viking://resources/demo"}}
        )

    sdk_transport(handle)
    client = AsyncHTTPClient(upload_mode=upload_mode)
    await client.initialize()
    try:
        assert (await client.add_resource(str(source)))["root_uri"] == "viking://resources/demo"
    finally:
        await client.close()

    assert [(r.method, r.url.path, r.headers.get("X-Gateway-Token")) for r in requests] == [
        ("GET", "/health", None),
        ("POST", "/api/v1/resources/temp_upload", "gateway-token"),
        ("POST", "/api/v1/resources", None),
        ("POST", "/api/v1/resources", "gateway-token"),
    ]
