# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.ovpack.operations import import_ovpack
from openviking_cli.session.user_id import UserIdentifier
from tests.misc.test_ovpack_import_policy import _write_ovpack_with_manifest


class _OverwriteVikingFS:
    def __init__(self, *, fail_suffix: str | None = None) -> None:
        self._ensure_destination_access = AsyncMock(return_value=True)
        self.files = {"viking://resources/demo/old.txt": b"old"}
        self.moves: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.fail_suffix = fail_suffix

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None) -> None:  # noqa: ANN001
        return None

    async def write_file_bytes(self, uri: str, data: bytes, ctx=None) -> None:  # noqa: ANN001
        if self.fail_suffix is not None and uri.endswith(self.fail_suffix):
            raise TimeoutError("storage timed out")
        self.files[uri] = data

    async def mv(self, old_uri: str, new_uri: str, ctx=None, **kwargs) -> None:  # noqa: ANN001
        self.moves.append((old_uri, new_uri))
        moved = {
            path: data
            for path, data in self.files.items()
            if path == old_uri or path.startswith(f"{old_uri}/")
        }
        for path in moved:
            del self.files[path]
        for path, data in moved.items():
            self.files[f"{new_uri}{path[len(old_uri) :]}"] = data

    async def rm(self, uri: str, recursive: bool = False, ctx=None) -> None:  # noqa: ANN001
        self.removed.append(uri)
        for path in [path for path in self.files if path == uri or path.startswith(f"{uri}/")]:
            del self.files[path]


@pytest.fixture
def request_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


@pytest.fixture
def ovpack_path() -> Path:
    fd, path = tempfile.mkstemp(suffix=".ovpack")
    os.close(fd)
    archive = Path(path)
    try:
        yield archive
    finally:
        archive.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_failed_overwrite_restores_the_previous_resource(
    ovpack_path: Path,
    request_ctx: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
):
    _write_ovpack_with_manifest(ovpack_path, "demo", {"a.txt": "new-a", "b.txt": "new-b"})
    monkeypatch.setattr(
        "openviking.storage.ovpack.operations._enqueue_direct_vectorization",
        AsyncMock(),
    )
    viking_fs = _OverwriteVikingFS(fail_suffix="/demo/b.txt")

    with pytest.raises(TimeoutError, match="storage timed out"):
        await import_ovpack(
            viking_fs,
            str(ovpack_path),
            "viking://resources",
            request_ctx,
            on_conflict="overwrite",
        )

    assert viking_fs.files == {"viking://resources/demo/old.txt": b"old"}
    backup_uri = viking_fs.moves[0][1]
    assert backup_uri.startswith("viking://resources/demo.ovpack-backup-")
    assert viking_fs.moves[1] == (backup_uri, "viking://resources/demo")
    assert viking_fs.removed == ["viking://resources/demo"]


@pytest.mark.asyncio
async def test_successful_overwrite_discards_the_backup(
    ovpack_path: Path,
    request_ctx: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
):
    _write_ovpack_with_manifest(ovpack_path, "demo", {"a.txt": "new-a", "b.txt": "new-b"})
    monkeypatch.setattr(
        "openviking.storage.ovpack.operations._enqueue_direct_vectorization",
        AsyncMock(),
    )
    viking_fs = _OverwriteVikingFS()

    await import_ovpack(
        viking_fs,
        str(ovpack_path),
        "viking://resources",
        request_ctx,
        on_conflict="overwrite",
    )

    assert viking_fs.files == {
        "viking://resources/demo/a.txt": b"new-a",
        "viking://resources/demo/b.txt": b"new-b",
    }
    backup_uri = viking_fs.moves[0][1]
    assert backup_uri.startswith("viking://resources/demo.ovpack-backup-")
    assert viking_fs.removed == [backup_uri]
