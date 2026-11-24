# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the resources-only WebDAV adapter."""

import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import httpx

from openviking.utils.summarizer import Summarizer


def _dav_display_names(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    return [node.text or "" for node in root.findall(".//{DAV:}displayname")]


def _webdav_path_from_uri(uri: str) -> str:
    prefix = "viking://resources"
    assert uri.startswith(prefix)
    return uri[len(prefix) :].lstrip("/")


async def test_webdav_get_content_length_uses_delivered_body_size(client, service, monkeypatch):
    uri = "viking://resources/scratch/notes.md"
    body = "Hello 你好".encode()
    await service.viking_fs.write_file_bytes(uri, body)
    stat = service.fs.stat

    async def stale_stat(*args, **kwargs):
        return {**await stat(*args, **kwargs), "size": 105}

    monkeypatch.setattr(service.fs, "stat", stale_stat)
    reader = AsyncMock(wraps=service.fs.read_file_bytes)
    monkeypatch.setattr(service.fs, "read_file_bytes", reader)
    path = "/webdav/resources/scratch/notes.md"

    head = await client.head(path)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == "105"
    reader.assert_not_awaited()

    fetched = await client.get(path)
    assert fetched.status_code == 200
    assert fetched.content == body
    assert fetched.headers["content-length"] == str(len(body))
    assert fetched.headers["last-modified"] == head.headers["last-modified"]


async def test_webdav_options_advertises_dav(client: httpx.AsyncClient):
    resp = await client.request("OPTIONS", "/webdav/resources")
    assert resp.status_code == 204
    assert resp.headers["DAV"] == "1"
    assert "PROPFIND" in resp.headers["Allow"]


async def test_webdav_hides_internal_files_and_keeps_user_dotdirs(
    client_with_resource, service, request_context
):
    client, uri = client_with_resource
    path = f"/webdav/resources/{_webdav_path_from_uri(uri)}"
    hidden_names = (
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        ".path.ovlock",
        ".exact.ovlock.notes.md.0123abcd",
        ".sync_log.json",
        ".redirect.json",
    )
    assert (await client.request("MKCOL", f"{path}/.obsidian")).status_code == 201
    config = b'{"theme":"light"}'
    assert (await client.put(f"{path}/.obsidian/config.json", content=config)).status_code == 201

    # Stage real directory entries after the WebDAV writes that acquire content locks.
    physical_dir = service.viking_fs._uri_to_path(uri, ctx=request_context)
    for name in hidden_names:
        if name not in {".path.ovlock", ".exact.ovlock.notes.md.0123abcd"}:
            await service.viking_fs._async_agfs.write(f"{physical_dir}/{name}", b"internal")
    # The local lock backend uses its own token format and manages the marker.
    lease = await service.viking_fs._async_agfs.pathlock_acquire_tree(physical_dir)

    try:
        response = await client.request("PROPFIND", path, headers={"Depth": "1"})
        assert response.status_code == 207
        names = _dav_display_names(response.content)
        assert ".obsidian" in names
        assert "sample.md" in names
        assert set(hidden_names).isdisjoint(names)

        for name in hidden_names:
            assert (await client.get(f"{path}/{name}")).status_code == 404, name
        assert (await client.get(f"{path}/.obsidian/config.json")).content == config
    finally:
        await service.viking_fs._async_agfs.pathlock_release(lease)


async def test_webdav_put_create_get_and_replace_text_file(
    client: httpx.AsyncClient, service, monkeypatch
):
    mkcol_resp = await client.request("MKCOL", "/webdav/resources/scratch")
    assert mkcol_resp.status_code == 201

    create_resp = await client.request(
        "PUT",
        "/webdav/resources/scratch/notes.md",
        content="# Notes\n\nhello".encode("utf-8"),
    )
    assert create_resp.status_code == 201

    get_resp = await client.get("/webdav/resources/scratch/notes.md")
    assert get_resp.status_code == 200
    assert get_resp.text == "# Notes\n\nhello"
    assert get_resp.headers["content-length"] == str(len(get_resp.content))

    refresh = Summarizer.refresh_file_parent
    scheduled_refresh = False

    async def check_persisted_before_refresh(summarizer, *, file_uri, ctx, **kwargs):
        nonlocal scheduled_refresh
        assert file_uri == "viking://resources/scratch/notes.md"
        assert await service.viking_fs.read_file(file_uri, ctx=ctx) == "# Notes\n\nupdated"
        scheduled_refresh = True
        return await refresh(summarizer, file_uri=file_uri, ctx=ctx, **kwargs)

    monkeypatch.setattr(Summarizer, "refresh_file_parent", check_persisted_before_refresh)

    replace_resp = await client.request(
        "PUT",
        "/webdav/resources/scratch/notes.md",
        content="# Notes\n\nupdated".encode("utf-8"),
    )
    assert replace_resp.status_code == 204
    assert scheduled_refresh

    get_resp = await client.get("/webdav/resources/scratch/notes.md")
    assert get_resp.status_code == 200
    assert get_resp.text == "# Notes\n\nupdated"
    assert get_resp.headers["content-length"] == str(len(get_resp.content))
    await service.resources.wait_processed()
    overview = await service.viking_fs.read_file("viking://resources/scratch/.overview.md")
    assert "notes.md" in overview


async def test_webdav_put_rejects_non_utf8_content(client: httpx.AsyncClient):
    assert (await client.request("MKCOL", "/webdav/resources/binary-space")).status_code == 201

    resp = await client.request(
        "PUT",
        "/webdav/resources/binary-space/blob.bin",
        content=b"\xff\xfe\xfd",
    )

    assert resp.status_code == 415
    assert "UTF-8" in resp.text
    assert (await client.get("/webdav/resources/binary-space/blob.bin")).status_code == 404


async def test_webdav_move_and_delete_text_file(client: httpx.AsyncClient):
    assert (await client.request("MKCOL", "/webdav/resources/move-space")).status_code == 201
    assert (
        await client.request(
            "PUT",
            "/webdav/resources/move-space/source.md",
            content="source body".encode("utf-8"),
        )
    ).status_code == 201

    move_resp = await client.request(
        "MOVE",
        "/webdav/resources/move-space/source.md",
        headers={"Destination": "http://testserver/webdav/resources/move-space/renamed.md"},
    )
    assert move_resp.status_code == 201

    missing_resp = await client.get("/webdav/resources/move-space/source.md")
    assert missing_resp.status_code == 404

    moved_resp = await client.get("/webdav/resources/move-space/renamed.md")
    assert moved_resp.status_code == 200
    assert moved_resp.text == "source body"

    delete_resp = await client.request("DELETE", "/webdav/resources/move-space/renamed.md")
    assert delete_resp.status_code == 204

    deleted_resp = await client.get("/webdav/resources/move-space/renamed.md")
    assert deleted_resp.status_code == 404
