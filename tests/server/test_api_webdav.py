# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the resources-only WebDAV adapter."""

import xml.etree.ElementTree as ET

import httpx
from starlette.requests import Request

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import webdav as webdav_router
from openviking.server.routers.webdav import _ensure_exposed_path, _exposed_child_entries
from openviking.storage.expr import And, Eq
from openviking.utils.content_hash import content_md5
from openviking_cli.exceptions import NotFoundError


def _dav_display_names(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    return [node.text or "" for node in root.findall(".//{DAV:}displayname")]


def _webdav_path_from_uri(uri: str) -> str:
    prefix = "viking://resources"
    assert uri.startswith(prefix)
    return uri[len(prefix) :].lstrip("/")


async def test_webdav_get_content_length_uses_delivered_body_size(monkeypatch):
    class FakeFS:
        async def stat(self, uri: str, ctx=None):
            assert uri == "viking://resources/scratch/notes.md"
            return {
                "isDir": False,
                "name": "notes.md",
                "size": len(b"hello") + 100,
                "modTime": "",
            }

        async def read_file_bytes(self, uri: str, ctx=None):
            assert uri == "viking://resources/scratch/notes.md"
            return b"hello"

    class FakeService:
        fs = FakeFS()

    monkeypatch.setattr(webdav_router, "get_service", lambda: FakeService())
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/webdav/resources/scratch/notes.md",
            "headers": [],
        }
    )

    resp = await webdav_router.get_or_head(request, "scratch/notes.md", None)

    assert resp.body == b"hello"
    assert resp.headers["content-length"] == "5"


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
    assert ".path.ovlock" not in names
    assert ".sync_log.json" not in names
    assert ".redirect.json" not in names


async def test_webdav_rejects_direct_access_to_reserved_semantic_files(client_with_resource):
    client, uri = client_with_resource
    webdav_path = _webdav_path_from_uri(uri)

    resp = await client.get(f"/webdav/resources/{webdav_path}/.abstract.md")
    assert resp.status_code == 404


async def test_webdav_rejects_direct_access_to_multiwrite_internal_files(client_with_resource):
    client, uri = client_with_resource
    webdav_path = _webdav_path_from_uri(uri)

    for hidden_name in (".path.ovlock", ".sync_log.json", ".redirect.json"):
        resp = await client.get(f"/webdav/resources/{webdav_path}/{hidden_name}")
        assert resp.status_code == 404


def test_webdav_unit_rejects_direct_access_to_multiwrite_internal_files():
    for hidden_name in (
        ".path.ovlock",
        ".exact.ovlock.notes.md.0123abcd",
        ".sync_log.json",
        ".redirect.json",
    ):
        try:
            _ensure_exposed_path(f"workspace/{hidden_name}")
        except NotFoundError:
            continue
        raise AssertionError(f"{hidden_name} should be rejected")


def test_webdav_unit_filters_multiwrite_internal_files_from_children():
    entries = [
        {"name": ".obsidian"},
        {"name": ".path.ovlock"},
        {"name": ".exact.ovlock.notes.md.0123abcd"},
        {"name": ".sync_log.json"},
        {"name": ".redirect.json"},
        {"name": "notes.md"},
    ]
    filtered = _exposed_child_entries(entries)
    assert [entry["name"] for entry in filtered] == [".obsidian", "notes.md"]


async def test_webdav_put_create_get_and_replace_text_file(
    client: httpx.AsyncClient,
    service,
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

    replace_resp = await client.request(
        "PUT",
        "/webdav/resources/scratch/notes.md",
        content="# Notes\n\nupdated".encode("utf-8"),
    )
    assert replace_resp.status_code == 204

    get_resp = await client.get("/webdav/resources/scratch/notes.md")
    assert get_resp.status_code == 200
    assert get_resp.text == "# Notes\n\nupdated"
    assert get_resp.headers["content-length"] == str(len(get_resp.content))

    await service.resources.wait_processed()
    file_uri = "viking://resources/scratch/notes.md"
    ctx = RequestContext(user=service.user, role=Role.USER)
    records = await service.vikingdb_manager.filter(
        filter=And([Eq("uri", file_uri), Eq("level", 2)]),
        limit=10,
        output_fields=["uri", "level", "md5"],
        ctx=ctx,
    )
    assert len(records) == 1
    assert records[0]["md5"] == content_md5(b"# Notes\n\nupdated")


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
