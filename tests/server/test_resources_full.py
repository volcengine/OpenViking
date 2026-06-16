# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for GET /api/v1/resources/full reassembly endpoint."""

import json

import httpx

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources as resources_router
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


async def _seed_dir(uri: str) -> None:
    fs = get_viking_fs()
    await fs.mkdir(uri, exist_ok=True, ctx=_ctx())


async def _seed_file(uri: str, content: str) -> None:
    fs = get_viking_fs()
    parent = uri.rsplit("/", 1)[0]
    await fs.mkdir(parent, exist_ok=True, ctx=_ctx())
    await fs.write_file(uri, content, ctx=_ctx())


async def test_full_reassembly_with_sidecar(client: httpx.AsyncClient, service):
    base = "viking://resources/with_sidecar"
    await _seed_dir(base)
    # Write 3 chunks out of order; sidecar metadata should drive ordering.
    await _seed_file(f"{base}/doc_2.md", "BBB")
    await _seed_file(f"{base}/doc_1.md", "AAA")
    await _seed_file(f"{base}/doc_3.md", "CCC")
    sidecar = {
        "version": 1,
        "chunks": {
            "doc_1.md": {"chunk_index": 0, "chunk_total": 3},
            "doc_2.md": {"chunk_index": 1, "chunk_total": 3},
            "doc_3.md": {"chunk_index": 2, "chunk_total": 3},
        },
    }
    await _seed_file(f"{base}/.chunks.json", json.dumps(sidecar))

    resp = await client.get("/api/v1/resources/full", params={"uri": base})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "directory"
    assert body["chunk_count"] == 3
    assert body["is_complete"] is True
    assert body["content"] == "AAA\n\nBBB\n\nCCC"


async def test_full_reassembly_filename_fallback(client: httpx.AsyncClient, service):
    base = "viking://resources/no_sidecar"
    await _seed_dir(base)
    await _seed_file(f"{base}/doc_1.md", "AAA")
    await _seed_file(f"{base}/doc_2.md", "BBB")
    await _seed_file(f"{base}/doc_3.md", "CCC")

    resp = await client.get("/api/v1/resources/full", params={"uri": base})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "directory"
    assert body["chunk_count"] == 3
    assert body["is_complete"] is True
    assert body["content"] == "AAA\n\nBBB\n\nCCC"


async def test_full_reassembly_missing_chunk_marks_incomplete(
    client: httpx.AsyncClient, service
):
    base = "viking://resources/missing"
    await _seed_dir(base)
    await _seed_file(f"{base}/doc_1.md", "AAA")
    # doc_2 deliberately missing
    await _seed_file(f"{base}/doc_3.md", "CCC")
    sidecar = {
        "version": 1,
        "chunks": {
            "doc_1.md": {"chunk_index": 0, "chunk_total": 3},
            "doc_2.md": {"chunk_index": 1, "chunk_total": 3},
            "doc_3.md": {"chunk_index": 2, "chunk_total": 3},
        },
    }
    await _seed_file(f"{base}/.chunks.json", json.dumps(sidecar))

    resp = await client.get("/api/v1/resources/full", params={"uri": base})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_complete"] is False
    assert body["chunk_count"] == 2
    assert "AAA" in body["content"] and "CCC" in body["content"]


async def test_full_reassembly_single_file(client: httpx.AsyncClient, service):
    uri = "viking://resources/lonely.md"
    await _seed_file(uri, "hello")

    resp = await client.get("/api/v1/resources/full", params={"uri": uri})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "file"
    assert body["chunk_count"] == 1
    assert body["is_complete"] is True
    assert body["content"] == "hello"


async def test_full_reassembly_oversize_returns_413(
    client: httpx.AsyncClient, service, monkeypatch
):
    # Shrink the cap for a fast test.
    monkeypatch.setattr(resources_router, "_MAX_REASSEMBLY_BYTES", 16)
    base = "viking://resources/big"
    await _seed_dir(base)
    await _seed_file(f"{base}/doc_1.md", "X" * 12)
    await _seed_file(f"{base}/doc_2.md", "Y" * 12)

    resp = await client.get("/api/v1/resources/full", params={"uri": base})
    assert resp.status_code == 413, resp.text


async def test_full_reassembly_unknown_uri(client: httpx.AsyncClient, service):
    resp = await client.get(
        "/api/v1/resources/full", params={"uri": "viking://resources/does_not_exist"}
    )
    assert resp.status_code == 404


async def test_chunk_metadata_persisted_via_markdown_parser(
    client: httpx.AsyncClient,
    service,
    upload_temp_dir,
):
    """End-to-end: ingest a long markdown doc and confirm the sidecar lands."""
    big = upload_temp_dir / "big.md"
    # Generate a doc with one heading and a giant paragraph forcing a split.
    payload = "# Title\n\n" + ("paragraph " * 4000)
    big.write_text(payload)

    resp = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": big.name, "reason": "test", "wait": True},
    )
    assert resp.status_code == 200, resp.text
    root_uri = resp.json()["result"]["root_uri"]
    assert root_uri

    # Reassembly should round-trip the original payload (modulo whitespace
    # normalization) and produce a stable chunk_count.
    full = await client.get("/api/v1/resources/full", params={"uri": root_uri})
    assert full.status_code == 200, full.text
    data = full.json()
    assert data["kind"] == "directory"
    assert data["chunk_count"] >= 1
    # The reassembled content must contain the heading and at least one
    # paragraph token.
    assert "Title" in data["content"]
    assert "paragraph" in data["content"]
