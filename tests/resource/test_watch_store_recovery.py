# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FileWatchStore must not treat an unreadable snapshot as an empty one."""

import pytest

from openviking.resource.watch_store_file import FileWatchStore


class _FailingReadFS:
    """Minimal VikingFS stand-in whose snapshot reads always fail."""

    def __init__(self) -> None:
        self.written_uris: list[str] = []

    async def read_file(self, uri, ctx=None, **kwargs):
        del ctx, kwargs
        raise OSError(f"storage temporarily unavailable: {uri}")

    async def write_file_bytes(self, uri, content, ctx=None, **kwargs):
        del content, ctx, kwargs
        self.written_uris.append(uri)


@pytest.mark.asyncio
async def test_initialize_fails_when_snapshot_is_unreadable():
    fake_fs = _FailingReadFS()
    store = FileWatchStore(fake_fs)

    with pytest.raises(OSError, match="storage temporarily unavailable"):
        await store.initialize()

    assert fake_fs.written_uris == []
