# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for signed ZIP artifacts."""

from __future__ import annotations

import asyncio
import io
import os
import stat
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from openviking_llamaparse_bridge.artifacts import ArtifactCache, ArtifactSigner, build_artifact
from openviking_llamaparse_bridge.llamaparse import LlamaParseError


class ArtifactClient:
    def __init__(self, result: dict[str, Any]):
        self.result = result
        self.downloaded_urls: list[str] = []

    async def get_job(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        assert job_id == "job-1"
        assert include_result is True
        return self.result

    async def download_asset(self, url: str) -> bytes:
        self.downloaded_urls.append(url)
        return b"image bytes"


def test_artifact_signatures_are_scoped_and_expire() -> None:
    signer = ArtifactSigner("secret", ttl_seconds=60)
    url = signer.create_url("https://bridge.test/base/", "job 1", now=100)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert parsed.path == "/base/artifacts/job%201.zip"
    assert signer.verify("job 1", query["expires"][0], query["signature"][0], now=160)
    assert not signer.verify("job 2", query["expires"][0], query["signature"][0], now=160)
    assert not signer.verify("job 1", query["expires"][0], query["signature"][0], now=161)
    assert not signer.verify("job 1", "invalid", query["signature"][0], now=100)


async def test_build_artifact_contains_markdown_and_images() -> None:
    client = ArtifactClient(
        {
            "job": {"status": "COMPLETED"},
            "markdown_full": "# Report\n\n![chart](chart.png)",
            "images_content_metadata": {
                "images": [
                    {
                        "filename": "chart.png",
                        "presigned_url": "https://assets.test/chart.png",
                    }
                ]
            },
        }
    )

    content = await build_artifact(client, "job-1")  # type: ignore[arg-type]

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.namelist() == ["content.md", "chart.png"]
        assert archive.read("content.md") == b"# Report\n\n![chart](chart.png)"
        assert archive.read("chart.png") == b"image bytes"
    assert client.downloaded_urls == ["https://assets.test/chart.png"]


async def test_build_artifact_downloads_images_concurrently() -> None:
    both_started = asyncio.Event()
    active_downloads = 0
    max_active_downloads = 0

    class ConcurrentClient(ArtifactClient):
        async def download_asset(self, url: str) -> bytes:
            nonlocal active_downloads, max_active_downloads
            active_downloads += 1
            max_active_downloads = max(max_active_downloads, active_downloads)
            if active_downloads == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            active_downloads -= 1
            return url.encode()

    client = ConcurrentClient(
        {
            "job": {"status": "COMPLETED"},
            "markdown_full": "content",
            "images_content_metadata": {
                "images": [
                    {"filename": "a.png", "presigned_url": "https://assets.test/a"},
                    {"filename": "b.png", "presigned_url": "https://assets.test/b"},
                ]
            },
        }
    )

    content = await build_artifact(client, "job-1")  # type: ignore[arg-type]

    assert max_active_downloads == 2
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("a.png") == b"https://assets.test/a"
        assert archive.read("b.png") == b"https://assets.test/b"


async def test_artifact_cache_reads_and_expires_files(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path, ttl_seconds=60, max_bytes=1024)

    await cache.store("job-1", b"artifact")

    assert await cache.contains("job-1")
    assert await cache.read("job-1") == b"artifact"

    cached_path = next(tmp_path.glob("*.zip"))
    expired = time.time() - 61
    os.utime(cached_path, (expired, expired))

    assert not await cache.contains("job-1")
    assert await cache.read("job-1") is None


def test_artifact_cache_preserves_existing_directory_permissions(tmp_path: Path) -> None:
    cache_directory = tmp_path / "shared-cache"
    cache_directory.mkdir(mode=0o755)
    cache_directory.chmod(0o755)

    ArtifactCache(cache_directory, ttl_seconds=60, max_bytes=1024)

    assert stat.S_IMODE(cache_directory.stat().st_mode) == 0o755


async def test_artifact_cache_does_not_delete_unrelated_zip_files(tmp_path: Path) -> None:
    unrelated = tmp_path / "user-data.zip"
    unrelated.write_bytes(b"unrelated")
    expired = time.time() - 61
    os.utime(unrelated, (expired, expired))
    cache = ArtifactCache(tmp_path, ttl_seconds=60, max_bytes=8)

    await cache.store("job-1", b"artifact")

    assert unrelated.read_bytes() == b"unrelated"


async def test_artifact_cache_rejects_symbolic_link_entries(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"private")
    cache_directory = tmp_path / "cache"
    cache_directory.mkdir()
    cache = ArtifactCache(cache_directory, ttl_seconds=60, max_bytes=1024)
    cache._path("job-1").symlink_to(outside)  # noqa: SLF001

    assert not await cache.contains("job-1")
    assert await cache.read("job-1") is None
    assert outside.read_bytes() == b"private"


async def test_artifact_cache_evicts_oldest_file_and_rejects_oversize(
    tmp_path: Path,
) -> None:
    cache = ArtifactCache(tmp_path, ttl_seconds=60, max_bytes=7)
    await cache.store("old", b"old")
    old_path = next(tmp_path.glob("*.zip"))
    old_time = time.time() - 10
    os.utime(old_path, (old_time, old_time))

    await cache.store("new", b"newer")

    assert await cache.read("old") is None
    assert await cache.read("new") == b"newer"
    with pytest.raises(LlamaParseError, match="exceeds the configured cache size") as error:
        await cache.store("large", b"too large")
    assert error.value.status_code == 507


async def test_build_artifact_joins_page_markdown() -> None:
    client = ArtifactClient(
        {
            "job": {"status": "COMPLETED"},
            "markdown": {
                "pages": [
                    {"page_number": 1, "markdown": "page one", "success": True},
                    {"page_number": 2, "markdown": "page two", "success": True},
                ]
            },
        }
    )

    content = await build_artifact(client, "job-1")  # type: ignore[arg-type]

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("content.md") == b"page one\n\npage two"


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({"job": {"status": "RUNNING"}}, "not complete: RUNNING"),
        ({"job": {"status": "COMPLETED"}}, "has no Markdown result"),
        (
            {
                "job": {"status": "COMPLETED"},
                "markdown_full": "content",
                "images_content_metadata": {
                    "images": [
                        {"filename": "../secret.png", "presigned_url": "https://assets.test/a"}
                    ]
                },
            },
            "unsafe filename",
        ),
        (
            {
                "job": {"status": "COMPLETED"},
                "markdown_full": "content",
                "images_content_metadata": {
                    "images": [
                        {"filename": "image.png", "presigned_url": "https://assets.test/a"},
                        {"filename": "IMAGE.PNG", "presigned_url": "https://assets.test/b"},
                    ]
                },
            },
            "duplicate image",
        ),
    ],
)
async def test_build_artifact_rejects_invalid_results(result: dict[str, Any], message: str) -> None:
    with pytest.raises(LlamaParseError, match=message):
        await build_artifact(ArtifactClient(result), "job-1")  # type: ignore[arg-type]
