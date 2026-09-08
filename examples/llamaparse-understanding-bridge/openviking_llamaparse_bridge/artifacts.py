# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Create signed Understanding API artifacts from LlamaParse results."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import os
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple
from urllib.parse import quote, urlencode

from .llamaparse import LlamaParseClient, LlamaParseError

CACHE_FILENAME_PREFIX = "artifact-"
CACHE_DIGEST_LENGTH = 64
LOWERCASE_HEX_DIGITS = frozenset("0123456789abcdef")


class ArtifactSigner:
    """Issue and verify short-lived artifact download URLs."""

    def __init__(self, secret: str, ttl_seconds: int):
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds

    def _signature(self, job_id: str, expires: int) -> str:
        message = f"{job_id}:{expires}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def create_url(self, public_url: str, job_id: str, *, now: Optional[int] = None) -> str:
        expires = (int(time.time()) if now is None else now) + self._ttl_seconds
        query = urlencode({"expires": expires, "signature": self._signature(job_id, expires)})
        return f"{public_url.rstrip('/')}/artifacts/{quote(job_id, safe='')}.zip?{query}"

    def verify(
        self, job_id: str, expires: str, signature: str, *, now: Optional[int] = None
    ) -> bool:
        try:
            expiry = int(expires)
        except (TypeError, ValueError):
            return False
        current_time = int(time.time()) if now is None else now
        expected = self._signature(job_id, expiry)
        return current_time <= expiry and hmac.compare_digest(expected, signature)


class ArtifactCache:
    """Store completed ZIP files in a bounded local cache."""

    def __init__(self, directory: Path, ttl_seconds: int, max_bytes: int):
        self._directory = directory
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._lock = asyncio.Lock()
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._directory.is_symlink():
            raise ValueError("artifact cache directory must not be a symbolic link")

    def _path(self, job_id: str) -> Path:
        digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
        return self._directory / f"{CACHE_FILENAME_PREFIX}{digest}.zip"

    def _entries(self, now: float) -> list[Tuple[Path, int, float]]:
        entries: list[Tuple[Path, int, float]] = []
        for path in self._directory.glob(f"{CACHE_FILENAME_PREFIX}*.zip"):
            digest = path.stem.removeprefix(CACHE_FILENAME_PREFIX)
            if (
                len(digest) != CACHE_DIGEST_LENGTH
                or not set(digest) <= LOWERCASE_HEX_DIGITS
                or path.is_symlink()
            ):
                continue
            try:
                stat = path.stat()
                if now - stat.st_mtime > self._ttl_seconds:
                    path.unlink(missing_ok=True)
                    continue
                entries.append((path, stat.st_size, stat.st_mtime))
            except FileNotFoundError:
                continue
        return entries

    def _prune(self, now: float, protected: Optional[Path] = None) -> None:
        entries = self._entries(now)
        total_bytes = sum(size for _, size, _ in entries)
        for path, size, _ in sorted(entries, key=lambda entry: entry[2]):
            if total_bytes <= self._max_bytes:
                break
            if path == protected:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            total_bytes -= size

    def _contains(self, job_id: str) -> bool:
        now = time.time()
        self._prune(now)
        path = self._path(job_id)
        if path.is_symlink() or not path.is_file():
            return False
        os.utime(path, (now, now))
        return True

    async def contains(self, job_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._contains, job_id)

    def _read(self, job_id: str) -> Optional[bytes]:
        now = time.time()
        self._prune(now)
        path = self._path(job_id)
        if path.is_symlink():
            return None
        try:
            content = path.read_bytes()
            os.utime(path, (now, now))
            return content
        except FileNotFoundError:
            return None

    async def read(self, job_id: str) -> Optional[bytes]:
        async with self._lock:
            return await asyncio.to_thread(self._read, job_id)

    def _store(self, job_id: str, content: bytes) -> None:
        if len(content) > self._max_bytes:
            raise LlamaParseError(507, "artifact exceeds the configured cache size")

        target = self._path(job_id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._directory, prefix=".artifact-", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

        now = time.time()
        os.utime(target, (now, now))
        self._prune(now, protected=target)

    async def store(self, job_id: str, content: bytes) -> None:
        async with self._lock:
            await asyncio.to_thread(self._store, job_id, content)


def _markdown(result: Dict[str, Any]) -> str:
    full = result.get("markdown_full")
    if isinstance(full, str) and full.strip():
        return full

    markdown = result.get("markdown")
    pages = markdown.get("pages") if isinstance(markdown, dict) else None
    if isinstance(pages, list):
        content = [page.get("markdown") for page in pages if isinstance(page, dict)]
        text = "\n\n".join(part for part in content if isinstance(part, str) and part.strip())
        if text:
            return text
    raise LlamaParseError(502, "completed LlamaParse job has no Markdown result")


def _images(result: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    metadata = result.get("images_content_metadata")
    images = metadata.get("images") if isinstance(metadata, dict) else None
    return images if isinstance(images, list) else []


def _safe_image_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LlamaParseError(502, "LlamaParse image has no filename")
    name = value.strip()
    if any(ord(character) < 32 for character in name) or ":" in name:
        raise LlamaParseError(502, "LlamaParse image has an unsafe filename")
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or len(path.parts) != 1 or path.name in {".", ".."}:
        raise LlamaParseError(502, "LlamaParse image has an unsafe filename")
    return path.name


async def build_artifact(client: LlamaParseClient, job_id: str) -> bytes:
    """Build the ZIP layout consumed by OpenViking's Understanding parser."""
    result = await client.get_job(job_id, include_result=True)
    job = result.get("job")
    status = str(job.get("status", "")).upper() if isinstance(job, dict) else ""
    if status != "COMPLETED":
        raise LlamaParseError(409, f"LlamaParse job is not complete: {status or 'UNKNOWN'}")

    used_names = {"content.md"}
    image_sources: list[Tuple[str, str]] = []
    for image in _images(result):
        if not isinstance(image, dict):
            raise LlamaParseError(502, "LlamaParse returned invalid image metadata")
        name = _safe_image_name(image.get("filename"))
        url = image.get("presigned_url")
        normalized_name = name.casefold()
        if normalized_name in used_names:
            raise LlamaParseError(502, f"LlamaParse returned duplicate image: {name}")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise LlamaParseError(502, f"LlamaParse image has no secure download URL: {name}")
        image_sources.append((name, url))
        used_names.add(normalized_name)

    semaphore = asyncio.Semaphore(8)

    async def download(source: Tuple[str, str]) -> Tuple[str, bytes]:
        name, url = source
        async with semaphore:
            return name, await client.download_asset(url)

    tasks = [asyncio.create_task(download(source)) for source in image_sources]
    try:
        images: Sequence[Tuple[str, bytes]] = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.md", _markdown(result).encode("utf-8"))
        for name, content in images:
            archive.writestr(name, content)
    return output.getvalue()
