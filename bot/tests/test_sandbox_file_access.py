# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for bounded local and remote sandbox file access."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.sandbox.backends.aiosandbox import AioSandboxBackend
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend
from vikingbot.sandbox.base import SandboxFileInfo


def _direct_backend(workspace: Path) -> DirectBackend:
    return DirectBackend(SimpleNamespace(restrict_workspaces={}), "test", workspace)


@pytest.mark.asyncio
async def test_local_workspace_listing_and_reads_are_bounded(tmp_path: Path):
    backend = _direct_backend(tmp_path)
    await backend.start()
    (tmp_path / ".hidden").write_bytes(b"h")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "artifact.bin").write_bytes(b"artifact")

    assert await backend.list_files(max_entries=3) == [
        SandboxFileInfo(path=".hidden", size=1),
        SandboxFileInfo(path="nested/artifact.bin", size=8),
    ]
    assert await backend.read_file_bytes("nested/artifact.bin", max_bytes=8) == b"artifact"
    with pytest.raises(ValueError, match="7-byte read limit"):
        await backend.read_file_bytes("nested/artifact.bin", max_bytes=7)
    with pytest.raises(ValueError, match="inventory exceeds 2 entries"):
        await backend.list_files(max_entries=2)


class _AioFileClient:
    def __init__(self, *, truncated=False):
        self.list_calls = []
        self.glob_calls = []
        self.download_calls = []
        self.truncated = truncated
        self.entries = {
            "/home/gem": [
                SimpleNamespace(name="artifact.bin", is_directory=False, size=6),
                SimpleNamespace(name="nested", is_directory=True, size=0),
            ],
            "/home/gem/nested": [
                SimpleNamespace(name="page.md", is_directory=False, size=4),
            ],
        }

    async def list_path(self, **kwargs):
        self.list_calls.append(kwargs)
        return SimpleNamespace(data=SimpleNamespace(files=self.entries[kwargs["path"]]))

    async def glob_files(self, **kwargs):
        self.glob_calls.append(kwargs)
        return SimpleNamespace(
            data=SimpleNamespace(
                files=[
                    SimpleNamespace(
                        path="/home/gem",
                        is_directory=True,
                        size=0,
                    ),
                    SimpleNamespace(
                        path="/home/gem/artifact.bin",
                        is_directory=False,
                        size=6,
                    ),
                    SimpleNamespace(
                        path="/home/gem/nested",
                        is_directory=True,
                        size=0,
                    ),
                    SimpleNamespace(
                        path="/home/gem/nested/page.md",
                        is_directory=False,
                        size=4,
                    ),
                ],
                total_count=5 if self.truncated else 4,
                truncated=self.truncated,
            )
        )

    async def download_file(self, *, path):
        self.download_calls.append(path)
        yield b"abc"
        yield b"def"


def _aio_backend(tmp_path: Path, file_client: _AioFileClient) -> AioSandboxBackend:
    config = SimpleNamespace(
        backends=SimpleNamespace(aiosandbox=SimpleNamespace(base_url="http://sandbox"))
    )
    backend = AioSandboxBackend(config, "test", tmp_path)
    backend._client = SimpleNamespace(file=file_client)
    return backend


@pytest.mark.asyncio
async def test_aiosandbox_lists_and_streams_from_remote_workspace(tmp_path: Path):
    files = _AioFileClient()
    backend = _aio_backend(tmp_path, files)

    assert await backend.list_files(max_entries=3) == [
        SandboxFileInfo(path="artifact.bin", size=6),
        SandboxFileInfo(path="nested/page.md", size=4),
    ]
    assert files.glob_calls == [
        {
            "path": "/home/gem",
            "pattern": "**",
            "include_hidden": True,
            "files_only": False,
            "include_metadata": True,
            "max_results": 4,
            "sort_by": "path",
        },
    ]
    assert await backend.read_file_bytes("artifact.bin", max_bytes=6) == b"abcdef"
    with pytest.raises(ValueError, match="5-byte read limit"):
        await backend.read_file_bytes("artifact.bin", max_bytes=5)
    assert files.download_calls == ["/home/gem/artifact.bin", "/home/gem/artifact.bin"]


@pytest.mark.asyncio
async def test_aiosandbox_inventory_uses_the_remote_result_limit(tmp_path: Path):
    files = _AioFileClient(truncated=True)
    backend = _aio_backend(tmp_path, files)

    with pytest.raises(ValueError, match="inventory exceeds 3 entries"):
        await backend.list_files(max_entries=3)
    assert files.glob_calls[0]["max_results"] == 4


class _OpenSandboxFiles:
    def __init__(self):
        self.read_calls = []

    async def read_bytes_stream(self, path, *, range_header):
        self.read_calls.append((path, range_header))
        payload = b"abcdef"

        async def chunks():
            yield payload[:3]
            yield payload[3:]

        return chunks()


class _OpenSandboxCommands:
    def __init__(self, *, overflow=False):
        self.overflow = overflow
        self.calls = []

    async def run(self, command, *, opts):
        self.calls.append((command, opts))
        payload = {
            "overflow": self.overflow,
            "files": [["artifact.bin", 6], ["nested/page.md", 4]],
        }
        return SimpleNamespace(
            error=None,
            logs=SimpleNamespace(stdout=[SimpleNamespace(text=json.dumps(payload))]),
        )


def _opensandbox_vke_backend(
    tmp_path: Path,
    file_client: _OpenSandboxFiles,
    commands: _OpenSandboxCommands,
) -> OpenSandboxBackend:
    backend = object.__new__(OpenSandboxBackend)
    backend._workspace = tmp_path
    backend._is_vke = True
    backend._sandbox = SimpleNamespace(files=file_client, commands=commands)
    return backend


@pytest.mark.asyncio
async def test_opensandbox_vke_uses_bounded_remote_inventory_and_range_reads(tmp_path: Path):
    files = _OpenSandboxFiles()
    commands = _OpenSandboxCommands()
    backend = _opensandbox_vke_backend(tmp_path, files, commands)

    assert await backend.list_files(max_entries=2) == [
        SandboxFileInfo(path="artifact.bin", size=6),
        SandboxFileInfo(path="nested/page.md", size=4),
    ]
    assert "python3 -c" in commands.calls[0][0]
    assert "limit = 2" in commands.calls[0][0]
    assert await backend.read_file_bytes("artifact.bin", max_bytes=6) == b"abcdef"
    with pytest.raises(ValueError, match="5-byte read limit"):
        await backend.read_file_bytes("artifact.bin", max_bytes=5)
    assert files.read_calls == [
        ("/workspace/artifact.bin", "bytes=0-6"),
        ("/workspace/artifact.bin", "bytes=0-5"),
    ]


@pytest.mark.asyncio
async def test_opensandbox_vke_inventory_stops_at_the_remote_limit(tmp_path: Path):
    commands = _OpenSandboxCommands(overflow=True)
    backend = _opensandbox_vke_backend(tmp_path, _OpenSandboxFiles(), commands)

    with pytest.raises(ValueError, match="inventory exceeds 1 entries"):
        await backend.list_files(max_entries=1)
    assert "limit = 1" in commands.calls[0][0]
