# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""send:// URIs must resolve to bare filenames inside the images directory.

The URI text is authored by the model at runtime; without a filename
allowlist a crafted remainder (``../../secrets``) reads arbitrary server
files and channel image delivery would exfiltrate them.
"""

from pathlib import Path

import pytest

from vikingbot.channels.base import BaseChannel


class _Channel(BaseChannel):
    """Minimal concrete channel exposing _parse_data_uri."""

    async def send(self, msg):  # pragma: no cover - abstract
        raise NotImplementedError

    async def receive(self):  # pragma: no cover - abstract
        raise NotImplementedError

    async def start(self):  # pragma: no cover - abstract
        raise NotImplementedError

    async def stop(self):  # pragma: no cover - abstract
        raise NotImplementedError


def _channel(tmp_path: Path) -> _Channel:
    ch = _Channel.__new__(_Channel)
    return ch


@pytest.fixture
def images_dir(tmp_path: Path, monkeypatch) -> Path:
    images = tmp_path / "data" / "images"
    images.mkdir(parents=True)
    (images / "cat.png").write_bytes(b"PNG-fake-bytes")

    import vikingbot.channels.base as base_mod

    monkeypatch.setattr(
        base_mod, "get_data_path", lambda: (tmp_path / "data")
    )
    return images


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "send://../secrets.txt",
        "send://../../etc/passwd",
        "send://..%2f..%2fx.png",
        "send:///etc/passwd",
        "send://sub/dir/cat.png",
        "send://.hidden",
        "send://",
        "send://.",
        "send://..",
    ],
)
async def test_send_uri_rejects_non_bare_filenames(images_dir, uri):
    with pytest.raises(ValueError, match="bare filename"):
        await _channel(images_dir)._parse_data_uri(uri)


@pytest.mark.asyncio
async def test_send_uri_accepts_bare_filename(images_dir):
    is_content, data = await _channel(images_dir)._parse_data_uri("send://cat.png")
    assert is_content is False
    assert data == b"PNG-fake-bytes"


@pytest.mark.asyncio
async def test_send_uri_missing_file_raises_not_traversal(images_dir):
    # A bare-but-absent filename is a normal missing file, not a rejection.
    with pytest.raises(FileNotFoundError):
        await _channel(images_dir)._parse_data_uri("send://nope.png")
