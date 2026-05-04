# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for index_resource recursive behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def _make_ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ADMIN,
    )


@pytest.fixture
def ctx():
    return _make_ctx()


@pytest.fixture
def fake_fs():
    fs = AsyncMock()
    fs.exists = AsyncMock(return_value=False)
    fs.ls = AsyncMock(return_value=[])
    return fs


async def test_index_resource_skips_subdirectories_when_recursive_false(ctx, fake_fs):
    """When recursive=False (default), subdirectories are skipped."""
    fake_fs.ls.return_value = [
        {"name": "subdir", "type": "directory", "uri": "viking://resources/root/subdir"},
        {"name": "file.md", "type": "file", "uri": "viking://resources/root/file.md"},
    ]

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=fake_fs),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
        patch(
            "openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock
        ) as mock_vec_file,
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://resources/root", ctx, recursive=False)

        mock_vec_file.assert_called_once()
        call_args = mock_vec_file.call_args
        assert call_args.kwargs["file_path"] == "viking://resources/root/file.md"


async def test_index_resource_recurses_into_subdirectories_when_recursive_true(ctx, fake_fs):
    """When recursive=True, subdirectories are recursively indexed."""

    async def fake_ls(uri, ctx=None):
        if uri == "viking://resources/root":
            return [
                {"name": "subdir", "type": "directory", "uri": "viking://resources/root/subdir"},
                {"name": "file1.md", "type": "file", "uri": "viking://resources/root/file1.md"},
            ]
        if uri == "viking://resources/root/subdir":
            return [
                {
                    "name": "file2.md",
                    "type": "file",
                    "uri": "viking://resources/root/subdir/file2.md",
                },
            ]
        return []

    fake_fs.ls = fake_ls

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=fake_fs),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
        patch(
            "openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock
        ) as mock_vec_file,
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://resources/root", ctx, recursive=True)

        assert mock_vec_file.call_count == 2
        indexed_uris = [c.kwargs["file_path"] for c in mock_vec_file.call_args_list]
        assert "viking://resources/root/file1.md" in indexed_uris
        assert "viking://resources/root/subdir/file2.md" in indexed_uris


async def test_index_resource_default_recursive_is_false(ctx, fake_fs):
    """Default value of recursive parameter is False."""
    fake_fs.ls.return_value = [
        {"name": "subdir", "type": "directory", "uri": "viking://resources/root/subdir"},
        {"name": "file.md", "type": "file", "uri": "viking://resources/root/file.md"},
    ]

    subdir_ls_called = {"value": False}

    original_ls = fake_fs.ls

    async def tracking_ls(uri, ctx=None):
        if uri == "viking://resources/root/subdir":
            subdir_ls_called["value"] = True
        return await original_ls(uri, ctx) if isinstance(original_ls, AsyncMock) else []

    fake_fs.ls = tracking_ls

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=fake_fs),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
        patch("openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock),
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://resources/root", ctx)

        assert not subdir_ls_called["value"]


async def test_index_resource_recursive_with_isDir_flag(ctx, fake_fs):
    """Subdirectories identified by isDir=True are also recursed into."""

    async def fake_ls(uri, ctx=None):
        if uri == "viking://resources/root":
            return [
                {"name": "nested", "isDir": True, "uri": "viking://resources/root/nested"},
                {"name": "top.md", "type": "file", "uri": "viking://resources/root/top.md"},
            ]
        if uri == "viking://resources/root/nested":
            return [
                {
                    "name": "deep.md",
                    "type": "file",
                    "uri": "viking://resources/root/nested/deep.md",
                },
            ]
        return []

    fake_fs.ls = fake_ls

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=fake_fs),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
        patch(
            "openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock
        ) as mock_vec_file,
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://resources/root", ctx, recursive=True)

        assert mock_vec_file.call_count == 2
        indexed_uris = [c.kwargs["file_path"] for c in mock_vec_file.call_args_list]
        assert "viking://resources/root/top.md" in indexed_uris
        assert "viking://resources/root/nested/deep.md" in indexed_uris


async def test_index_resource_recursive_deep_nesting(ctx, fake_fs):
    """Recursive indexing works with 3+ levels of nesting."""

    async def fake_ls(uri, ctx=None):
        if uri == "viking://resources/l0":
            return [
                {"name": "l1", "type": "directory", "uri": "viking://resources/l0/l1"},
                {"name": "f0.md", "type": "file", "uri": "viking://resources/l0/f0.md"},
            ]
        if uri == "viking://resources/l0/l1":
            return [
                {"name": "l2", "type": "directory", "uri": "viking://resources/l0/l1/l2"},
                {"name": "f1.md", "type": "file", "uri": "viking://resources/l0/l1/f1.md"},
            ]
        if uri == "viking://resources/l0/l1/l2":
            return [
                {"name": "f2.md", "type": "file", "uri": "viking://resources/l0/l1/l2/f2.md"},
            ]
        return []

    fake_fs.ls = fake_ls

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=fake_fs),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
        patch(
            "openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock
        ) as mock_vec_file,
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://resources/l0", ctx, recursive=True)

        assert mock_vec_file.call_count == 3
        indexed_uris = [c.kwargs["file_path"] for c in mock_vec_file.call_args_list]
        assert "viking://resources/l0/f0.md" in indexed_uris
        assert "viking://resources/l0/l1/f1.md" in indexed_uris
        assert "viking://resources/l0/l1/l2/f2.md" in indexed_uris


async def test_index_resource_recursive_empty_subdirectory(ctx, fake_fs):
    """Recursive indexing handles empty subdirectories without error."""

    async def fake_ls(uri, ctx=None):
        if uri == "viking://resources/root":
            return [
                {"name": "empty", "type": "directory", "uri": "viking://resources/root/empty"},
                {"name": "file.md", "type": "file", "uri": "viking://resources/root/file.md"},
            ]
        if uri == "viking://resources/root/empty":
            return []
        return []

    fake_fs.ls = fake_ls

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=fake_fs),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
        patch(
            "openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock
        ) as mock_vec_file,
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://resources/root", ctx, recursive=True)

        assert mock_vec_file.call_count == 1
        assert mock_vec_file.call_args.kwargs["file_path"] == "viking://resources/root/file.md"
