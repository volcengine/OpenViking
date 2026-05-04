# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for POST /api/v1/resources/temp_upload_signed."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from openviking.server.upload_token_store import upload_token_store


@pytest.fixture(autouse=True)
def _reset_token_store():
    upload_token_store.clear()
    yield
    upload_token_store.clear()


def _issue(account_id: str = "acct", user_id: str = "user", suffix: str = ".md"):
    tfid = f"upload_{int(time.time() * 1000)}{suffix}"
    token, _ = upload_token_store.issue(account_id, user_id, tfid, ttl_seconds=600)
    return token, tfid


async def test_signed_upload_writes_file_to_per_tenant_subdir(
    client: httpx.AsyncClient, upload_temp_dir: Path
):
    token, tfid = _issue()
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token, "temp_file_id": tfid},
        files={"file": (tfid, b"hello world", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"temp_file_id": tfid}

    written = upload_temp_dir / "acct" / "user" / tfid
    assert written.is_file()
    assert written.read_bytes() == b"hello world"

    meta_path = upload_temp_dir / "acct" / "user" / f"{tfid}.ov_upload.meta"
    assert meta_path.is_file()


async def test_signed_upload_burns_token_on_use(client: httpx.AsyncClient, upload_temp_dir: Path):
    token, tfid = _issue()
    resp1 = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token, "temp_file_id": tfid},
        files={"file": (tfid, b"first", "text/plain")},
    )
    assert resp1.status_code == 200

    resp2 = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token, "temp_file_id": tfid},
        files={"file": (tfid, b"second", "text/plain")},
    )
    assert resp2.status_code == 401


async def test_signed_upload_unknown_token(client: httpx.AsyncClient, upload_temp_dir: Path):
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": "ZZZZZZ", "temp_file_id": "upload_abc.md"},
        files={"file": ("upload_abc.md", b"x", "text/plain")},
    )
    assert resp.status_code == 401


async def test_signed_upload_rejects_path_traversal_temp_file_id(
    client: httpx.AsyncClient, upload_temp_dir: Path
):
    # Issue a valid token but submit a tfid that doesn't match the regex
    token, _ = upload_token_store.issue("a", "u", "../escape", ttl_seconds=60)
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token, "temp_file_id": "../escape"},
        files={"file": ("x", b"y", "text/plain")},
    )
    assert resp.status_code == 400


async def test_signed_upload_token_for_different_temp_file_id(
    client: httpx.AsyncClient, upload_temp_dir: Path
):
    token, _ = upload_token_store.issue("a", "u", "upload_AAA.md", ttl_seconds=60)
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token, "temp_file_id": "upload_BBB.md"},
        files={"file": ("upload_BBB.md", b"y", "text/plain")},
    )
    assert resp.status_code == 401


async def test_signed_upload_oversize_rejected(
    client: httpx.AsyncClient, upload_temp_dir: Path, app
):
    # Tighten max_bytes via app.state.config
    app.state.config.upload_signed_max_bytes = 16

    token, tfid = _issue()
    resp = await client.post(
        "/api/v1/resources/temp_upload_signed",
        params={"token": token, "temp_file_id": tfid},
        files={"file": (tfid, b"x" * 64, "text/plain")},
    )
    assert resp.status_code == 413
    # File should not have been retained partially
    assert not (upload_temp_dir / "acct" / "user" / tfid).exists()


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
    # And NOT under any per-tenant subdir
    assert not any((upload_temp_dir / sub).exists() for sub in ("acct", "user"))
