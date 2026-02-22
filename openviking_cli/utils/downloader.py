# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
URL downloader for OpenViking.

Provides basic file download functionality.
For URL content parsing, use HTMLParser instead.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional, Tuple

from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.storage import StoragePath, get_storage

logger = get_logger(__name__)


def is_url(data: str) -> bool:
    """Check if string is a URL."""
    if not isinstance(data, str):
        return False
    return data.startswith(("http://", "https://"))


async def download_file(
    url: str,
    storage: Optional[StoragePath] = None,
    timeout: float = 30.0,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Download a file from URL.

    Args:
        url: URL to download
        storage: Storage path manager
        timeout: Request timeout

    Returns:
        Tuple of (file_path, error_message)
    """
    try:
        import httpx
    except ImportError:
        return None, "httpx is required. Install with: pip install httpx"

    storage = storage or get_storage()
    storage.ensure_dirs()

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        # Determine filename
        content_type = response.headers.get("content-type", "").lower()
        filename = _generate_filename(url)

        # Determine extension from content type
        if "pdf" in content_type:
            ext = ".pdf"
        elif "html" in content_type:
            ext = ".html"
        elif "json" in content_type:
            ext = ".json"
        elif "text" in content_type:
            ext = ".txt"
        else:
            ext = ".bin"

        file_path = storage.get_download_path(filename, ext)
        file_path.write_bytes(response.content)

        logger.info(f"Downloaded: {url} -> {file_path}")
        return file_path, None

    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        return None, str(e)


def _generate_filename(url: str, max_length: int = 50) -> str:
    """Generate filename from URL, hash & shorten if too long."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path

    if path and path != "/":
        name = Path(path).stem
        name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name)
        name = re.sub(r"_+", "_", name)
        if not name:
            return "download"
        if len(name) > max_length:
            hash_suffix = hashlib.sha256(url.encode()).hexdigest()[:8]
            return f"{name[: max_length - 9]}_{hash_suffix}"
        return name

    host = parsed.netloc.replace(".", "_")
    if not host:
        return "download"
    if len(host) > max_length:
        hash_suffix = hashlib.sha256(url.encode()).hexdigest()[:8]
        return f"{host[: max_length - 9]}_{hash_suffix}"
    return host
