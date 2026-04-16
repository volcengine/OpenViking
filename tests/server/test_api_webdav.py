# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the resources-only WebDAV adapter."""

import xml.etree.ElementTree as ET

import httpx


def _dav_display_names(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    return [node.text or "" for node in root.findall(".//{DAV:}displayname")]


def _webdav_path_from_uri(uri: str) -> str:
    prefix = "viking://resources"
    assert uri.startswith(prefix)
    return uri[len(prefix) :].lstrip("/")


async def test_webdav_options_advertises_dav(client: httpx.AsyncClient):
    resp = await client.request("OPTIONS", "/webdav/resources")
    assert resp.status_code == 204
    assert resp.headers["DAV"] == "1"
    assert "PROPFIND" in resp.headers["Allow"]


async def test_webdav_propfind_hides_reserved_files_but_keeps_user_dotdirs(client_with_resource):
    client, uri = client_with_resource
    webdav_path = _webdav_path_from_uri(uri)

    mkcol_resp = await client.request("MKCOL", f"/webdav/resources/{webdav_path}/.obsidian")
    assert mkcol_resp.status_code == 201

    put_resp = await client.request(
        "PUT",
        f"/webdav/resources/{webdav_path}/.obsidian/config.json",
        content='{"theme":"light"}'.encode("utf-8"),
    )
    assert put_resp.status_code == 201

    resp = await client.request(
        "PROPFIND",
        f"/webdav/resources/{webdav_path}",
        headers={"Depth": "1"},
    )
    assert resp.status_code == 207

    names = _dav_display_names(resp.content)
    assert ".obsidian" in names
    assert ".abstract.md" not in names
    assert ".overview.md" not in names
    assert ".relations.json" not in names


async def test_webdav_rejects_direct_access_to_reserved_semantic_files(client_with_resource):
    client, uri = client_with_resource
    webdav_path = _webdav_path_from_uri(uri)

    resp = await client.get(f"/webdav/resources/{webdav_path}/.abstract.md")
    assert resp.status_code == 404


async def test_webdav_put_create_get_and_replace_text_file(client: httpx.AsyncClient):
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

    replace_resp = await client.request(
        "PUT",
        "/webdav/resources/scratch/notes.md",
        content="# Notes\n\nupdated".encode("utf-8"),
    )
    assert replace_resp.status_code == 204

    get_resp = await client.get("/webdav/resources/scratch/notes.md")
    assert get_resp.status_code == 200
    assert get_resp.text == "# Notes\n\nupdated"


async def test_webdav_put_replace_reuses_direct_write_path(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    assert (await client.request("MKCOL", "/webdav/resources/replace-space")).status_code == 201
    assert (
        await client.request(
            "PUT",
            "/webdav/resources/replace-space/notes.md",
            content="first version".encode("utf-8"),
        )
    ).status_code == 201

    calls: list[tuple[str, object]] = []

    async def _unexpected_fs_write(*args, **kwargs):
        raise AssertionError("WebDAV replace should not use service.fs.write")

    async def _tracked_write_file(uri, content, ctx=None):
        calls.append(("write_file", uri, content))

    async def _tracked_summarize(resource_uris, ctx=None, **kwargs):
        calls.append(("summarize", tuple(resource_uris)))
        return {"status": "success"}

    monkeypatch.setattr(service.fs, "write", _unexpected_fs_write)
    monkeypatch.setattr(service.viking_fs, "write_file", _tracked_write_file)
    monkeypatch.setattr(service.resources, "summarize", _tracked_summarize)

    replace_resp = await client.request(
        "PUT",
        "/webdav/resources/replace-space/notes.md",
        content="second version".encode("utf-8"),
    )

    assert replace_resp.status_code == 204
    assert calls == [
        ("write_file", "viking://resources/replace-space/notes.md", "second version"),
        ("summarize", ("viking://resources/replace-space/notes.md",)),
    ]


async def test_webdav_put_rejects_non_utf8_content(client: httpx.AsyncClient):
    assert (await client.request("MKCOL", "/webdav/resources/binary-space")).status_code == 201

    resp = await client.request(
        "PUT",
        "/webdav/resources/binary-space/blob.bin",
        content=b"\xff\xfe\xfd",
    )

    assert resp.status_code == 415


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
