# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for POST /api/v1/resources/temp_upload_signed."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from openviking.server.upload_token_store import upload_token_store


@pytest.fixture(autouse=True)
def _reset_token_store():
    upload_token_store.clear()
    yield
    upload_token_store.clear()


def _issue(account_id: str = "acct", user_id: str = "user", agent_id: str = "agent"):
    token, _ = upload_token_store.issue(account_id, user_id, agent_id, ttl_seconds=600)
    return token


async def test_signed_upload_writes_file_and_returns_minted_tfid(
    client: httpx.AsyncClient, upload_temp_dir: Path
):
    token = _issue()
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token},
        files={"file": ("hello.md", b"hello world", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    tfid = body["temp_file_id"]
    # TempUploadStore in local mode mints names like upload_<hex>.<ext>
    assert tfid.startswith("upload_")

    # File lands in the flat-local TempUploadStore layout, not a per-tenant subdir.
    written = upload_temp_dir / tfid
    assert written.is_file()
    assert written.read_bytes() == b"hello world"

    meta_path = upload_temp_dir / f"{tfid}.ov_upload.meta"
    assert meta_path.is_file()


async def test_signed_upload_burns_token_on_use(client: httpx.AsyncClient, upload_temp_dir: Path):
    token = _issue()
    resp1 = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token},
        files={"file": ("a.txt", b"first", "text/plain")},
    )
    assert resp1.status_code == 200

    resp2 = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token},
        files={"file": ("a.txt", b"second", "text/plain")},
    )
    assert resp2.status_code == 401


async def test_signed_upload_unknown_token(client: httpx.AsyncClient, upload_temp_dir: Path):
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": "ZZZZZZ"},
        files={"file": ("upload_abc.md", b"x", "text/plain")},
    )
    assert resp.status_code == 401


async def test_signed_upload_oversize_rejected(
    client: httpx.AsyncClient, upload_temp_dir: Path, app
):
    """Size cap is enforced while streaming by TempUploadStore — same limit as /temp_upload."""
    app.state.config.temp_upload.shared_max_size_bytes = 16

    token = _issue()
    big = b"x" * 64
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token},
        files={"file": ("big.bin", big, "text/plain")},
    )
    assert resp.status_code == 413


async def test_legacy_temp_upload_still_writes_flat(
    client: httpx.AsyncClient, upload_temp_dir: Path
):
    """The CLI flow at POST /api/v1/resources/temp_upload must keep working unchanged."""
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("legacy.md", b"legacy", "text/plain")},
    )
    assert resp.status_code == 200
    tfid = resp.json()["result"]["temp_file_id"]
    assert (upload_temp_dir / tfid).is_file()
