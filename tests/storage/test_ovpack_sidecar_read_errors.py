# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.index_consistency import build_index_expectations
from openviking.storage.ovpack.index import read_text_if_exists
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _SidecarFS:
    def __init__(self, error: Exception | None = None, *, exists: bool = True) -> None:
        self.error = error
        self._exists = exists

    async def exists(self, uri: str, ctx=None) -> bool:  # noqa: ANN001
        return self._exists

    async def read_file(self, uri: str, ctx=None) -> bytes:  # noqa: ANN001
        if self.error is not None:
            raise self.error
        return b"abstract body"


@pytest.fixture
def request_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


async def test_consistency_expectation_propagates_sidecar_read_failure(
    request_ctx: RequestContext,
):
    viking_fs = _SidecarFS(TimeoutError("storage timed out"))
    entries = [{"rel_path": "", "isDir": True, "uri": "viking://resources/demo"}]

    with pytest.raises(TimeoutError, match="storage timed out"):
        await build_index_expectations(viking_fs, "viking://resources/demo", entries, request_ctx)


async def test_consistency_expectation_treats_a_missing_sidecar_as_empty(
    request_ctx: RequestContext,
):
    viking_fs = _SidecarFS(NotFoundError("viking://resources/demo/.abstract.md", "file"))
    entries = [{"rel_path": "", "isDir": True, "uri": "viking://resources/demo"}]

    expectations = await build_index_expectations(
        viking_fs, "viking://resources/demo", entries, request_ctx
    )

    assert expectations == ()


async def test_ovpack_sidecar_reader_propagates_storage_failure(
    request_ctx: RequestContext,
):
    viking_fs = _SidecarFS(TimeoutError("storage timed out"))

    with pytest.raises(TimeoutError, match="storage timed out"):
        await read_text_if_exists(viking_fs, "viking://resources/demo/.abstract.md", request_ctx)


async def test_ovpack_sidecar_reader_treats_absent_sidecar_as_empty(
    request_ctx: RequestContext,
):
    viking_fs = _SidecarFS(exists=False)

    assert (
        await read_text_if_exists(viking_fs, "viking://resources/demo/.abstract.md", request_ctx)
        == ""
    )
