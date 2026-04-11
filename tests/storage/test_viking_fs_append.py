import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.viking_fs import VikingFS


class _RaceAGFS:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}

    def read(self, path: str) -> bytes:
        if path not in self.contents:
            raise RuntimeError(f"not found: {path}")
        return self.contents[path]

    def write(self, path: str, data: bytes) -> None:
        self.contents[path] = data


@pytest.mark.asyncio
async def test_append_file_treats_runtime_not_found_as_missing() -> None:
    agfs = MagicMock()
    agfs.read.side_effect = RuntimeError(
        "not found: /local/default/session/default/session-1/messages.jsonl"
    )
    fs = VikingFS(agfs=agfs)
    fs._ensure_parent_dirs = AsyncMock()  # type: ignore[method-assign]

    await fs.append_file(
        "viking://session/default/session-1/messages.jsonl",
        '{"role":"user"}\n',
    )

    agfs.write.assert_called_once_with(
        "/local/default/session/default/session-1/messages.jsonl",
        b'{"role":"user"}\n',
    )


@pytest.mark.asyncio
async def test_append_file_still_raises_unrelated_runtime_errors() -> None:
    agfs = MagicMock()
    agfs.read.side_effect = RuntimeError("backend unavailable")
    fs = VikingFS(agfs=agfs)
    fs._ensure_parent_dirs = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(IOError, match="backend unavailable"):
        await fs.append_file(
            "viking://session/default/session-1/messages.jsonl",
            '{"role":"user"}\n',
        )


@pytest.mark.asyncio
async def test_append_file_serializes_concurrent_first_writes() -> None:
    agfs = _RaceAGFS()
    fs = VikingFS(agfs=agfs)

    async def slow_ensure_parent_dirs(_path: str) -> None:
        await asyncio.sleep(0.01)

    fs._ensure_parent_dirs = slow_ensure_parent_dirs  # type: ignore[method-assign]

    await asyncio.gather(
        fs.append_file("viking://session/default/session-1/messages.jsonl", "first\n"),
        fs.append_file("viking://session/default/session-1/messages.jsonl", "second\n"),
    )

    assert agfs.contents["/local/default/session/default/session-1/messages.jsonl"] in {
        b"first\nsecond\n",
        b"second\nfirst\n",
    }
