# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for VikingFS new methods.

Tests for:
- exists(): Check if URI exists
- copy_directory(): Recursively copy directory
- delete_temp(): Delete temporary directory
"""

import contextvars
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.viking_fs import VikingFS


def _create_viking_fs_mock():
    """Create a VikingFS instance with mocked AGFS backend."""
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = None
    fs.vector_store = None
    fs._uri_prefix = "viking://"
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx", default=None)
    return fs


@pytest.mark.asyncio
class TestVikingFSExists:
    """Test cases for VikingFS.exists() method."""

    async def test_exists_returns_true_when_uri_exists(self):
        """exists() should return True when URI exists."""
        fs = _create_viking_fs_mock()
        fs.stat = AsyncMock(return_value={"name": "test_file.txt", "isDir": False})

        result = await fs.exists("viking://temp/test_file.txt")

        assert result is True
        fs.stat.assert_awaited_once_with("viking://temp/test_file.txt", ctx=None)

    async def test_exists_returns_false_when_uri_not_found(self):
        """exists() should return False when URI does not exist."""
        fs = _create_viking_fs_mock()
        fs.stat = AsyncMock(side_effect=FileNotFoundError("Not found"))

        result = await fs.exists("viking://temp/nonexistent.txt")

        assert result is False
        fs.stat.assert_awaited_once_with("viking://temp/nonexistent.txt", ctx=None)

    async def test_exists_returns_false_on_any_exception(self):
        """exists() should return False on any exception, not just FileNotFoundError."""
        fs = _create_viking_fs_mock()
        fs.stat = AsyncMock(side_effect=PermissionError("Access denied"))

        result = await fs.exists("viking://temp/protected.txt")

        assert result is False


@pytest.mark.asyncio
class TestVikingFSCopyDirectory:
    """Test cases for VikingFS.copy_directory() method."""

    async def test_copy_directory_recursive(self):
        """copy_directory() should recursively copy directory contents."""
        fs = _create_viking_fs_mock()
        fs._ensure_access = MagicMock()
        fs._uri_to_path = MagicMock(
            side_effect=lambda uri, ctx=None: uri.replace("viking://", "/local/")
        )
        fs._ensure_parent_dirs = AsyncMock()

        mock_agfs_cp = MagicMock()

        with patch("openviking.storage.viking_fs.agfs_cp", mock_agfs_cp):
            await fs.copy_directory(
                "viking://temp/source_dir/",
                "viking://temp/dest_dir/",
            )

        fs._ensure_access.assert_any_call("viking://temp/source_dir/", None)
        fs._ensure_access.assert_any_call("viking://temp/dest_dir/", None)
        fs._ensure_parent_dirs.assert_awaited_once_with("/local/temp/dest_dir/")
        mock_agfs_cp.assert_called_once_with(
            fs.agfs,
            "/local/temp/source_dir/",
            "/local/temp/dest_dir/",
            recursive=True,
        )

    async def test_copy_directory_with_context(self):
        """copy_directory() should pass context to helper methods."""
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        fs = _create_viking_fs_mock()
        fs._ensure_access = MagicMock()
        fs._uri_to_path = MagicMock(
            side_effect=lambda uri, ctx=None: uri.replace("viking://", "/local/")
        )
        fs._ensure_parent_dirs = AsyncMock()

        ctx = RequestContext(
            user=UserIdentifier("acc1", "user1", "agent1"),
            role=Role.USER,
        )

        mock_agfs_cp = MagicMock()

        with patch("openviking.storage.viking_fs.agfs_cp", mock_agfs_cp):
            await fs.copy_directory(
                "viking://temp/source/",
                "viking://temp/dest/",
                ctx=ctx,
            )

        fs._ensure_access.assert_any_call("viking://temp/source/", ctx)
        fs._ensure_access.assert_any_call("viking://temp/dest/", ctx)


@pytest.mark.asyncio
class TestVikingFSDeleteTemp:
    """Test cases for VikingFS.delete_temp() method."""

    async def test_delete_temp_removes_directory_and_contents(self):
        """delete_temp() should remove directory and all its contents."""
        fs = _create_viking_fs_mock()
        fs._uri_to_path = MagicMock(return_value="/local/temp/test_temp")

        fs._ls_entries = MagicMock(
            return_value=[
                {"name": "file1.txt", "isDir": False},
                {"name": "subdir", "isDir": True},
            ]
        )

        fs.agfs.rm = MagicMock()

        call_count = [0]

        async def mock_delete_temp(uri, ctx=None):
            call_count[0] += 1
            if call_count[0] == 1:
                fs._ls_entries.return_value = [
                    {"name": "nested_file.txt", "isDir": False},
                ]
                await fs.delete_temp(uri, ctx=ctx)
            else:
                fs._ls_entries.return_value = []

        original_delete_temp = fs.delete_temp
        fs.delete_temp = mock_delete_temp

        await original_delete_temp("viking://temp/test_temp/")

        assert fs.agfs.rm.call_count >= 1

    async def test_delete_temp_handles_empty_directory(self):
        """delete_temp() should handle empty directory gracefully."""
        fs = _create_viking_fs_mock()
        fs._uri_to_path = MagicMock(return_value="/local/temp/empty_temp")
        fs._ls_entries = MagicMock(return_value=[])
        fs.agfs.rm = MagicMock()

        await fs.delete_temp("viking://temp/empty_temp/")

        fs.agfs.rm.assert_called_once_with("/local/temp/empty_temp")

    async def test_delete_temp_skips_dot_entries(self):
        """delete_temp() should skip . and .. entries."""
        fs = _create_viking_fs_mock()
        fs._uri_to_path = MagicMock(return_value="/local/temp/test_temp")
        fs._ls_entries = MagicMock(
            return_value=[
                {"name": ".", "isDir": True},
                {"name": "..", "isDir": True},
                {"name": "actual_file.txt", "isDir": False},
            ]
        )
        fs.agfs.rm = MagicMock()

        await fs.delete_temp("viking://temp/test_temp/")

        rm_calls = [call[0][0] for call in fs.agfs.rm.call_args_list]
        assert "/local/temp/test_temp/actual_file.txt" in rm_calls
        assert "/local/temp/test_temp/." not in rm_calls
        assert "/local/temp/test_temp/.." not in rm_calls

    async def test_delete_temp_logs_warning_on_error(self):
        """delete_temp() should log warning but not raise on error."""
        fs = _create_viking_fs_mock()
        fs._uri_to_path = MagicMock(return_value="/local/temp/error_temp")
        fs._ls_entries = MagicMock(side_effect=Exception("AGFS error"))

        with patch("openviking.storage.viking_fs.logger") as mock_logger:
            await fs.delete_temp("viking://temp/error_temp/")

            mock_logger.warning.assert_called_once()
            assert "Failed to delete temp" in mock_logger.warning.call_args[0][0]
