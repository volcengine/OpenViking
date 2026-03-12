# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for SemanticProcessor incremental update and diff calculation.

Tests for:
- _detect_file_type(): Detect file type based on extension
- _collect_tree_info(): Collect directory tree information
- _compute_diff(): Compute directory differences
- _check_file_content_changed(): Check file content changes
- _execute_sync_operations(): Execute sync operations
- _create_sync_diff_callback(): Create sync diff callback
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.parsers.constants import (
    FILE_TYPE_CODE,
    FILE_TYPE_DOCUMENTATION,
    FILE_TYPE_OTHER,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_processor import DiffResult, SemanticProcessor
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    """Fake VikingFS for testing."""

    def __init__(self):
        self._tree = {}
        self._file_contents = {}
        self.deleted_files = []
        self.deleted_dirs = []
        self.moved_files = []
        self.created_dirs = []

    def set_tree(self, tree):
        self._tree = tree

    def set_file_contents(self, contents):
        self._file_contents = contents

    async def ls(self, uri, ctx=None):
        return self._tree.get(uri.rstrip("/"), [])

    async def read_file(self, uri, ctx=None):
        return self._file_contents.get(uri, "")

    async def rm(self, uri, recursive=False, ctx=None):
        if recursive:
            self.deleted_dirs.append(uri)
        else:
            self.deleted_files.append(uri)

    async def mv(self, src, dst, ctx=None):
        self.moved_files.append((src, dst))

    async def mkdir(self, uri, exist_ok=True, ctx=None):
        self.created_dirs.append(uri)


@pytest.fixture
def processor():
    """Create a SemanticProcessor instance for testing."""
    return SemanticProcessor(max_concurrent_llm=10)


@pytest.fixture
def fake_fs():
    """Create a fake VikingFS instance."""
    return FakeVikingFS()


@pytest.fixture
def ctx():
    """Create a RequestContext for testing."""
    return RequestContext(
        user=UserIdentifier("test_account", "test_user", "test_agent"),
        role=Role.USER,
    )


class TestDetectFileType:
    """Test cases for _detect_file_type() method."""

    def test_detect_python_file(self, processor):
        result = processor._detect_file_type("main.py")
        assert result == FILE_TYPE_CODE

    def test_detect_javascript_file(self, processor):
        result = processor._detect_file_type("app.js")
        assert result == FILE_TYPE_CODE

    def test_detect_typescript_file(self, processor):
        result = processor._detect_file_type("utils.ts")
        assert result == FILE_TYPE_CODE

    def test_detect_java_file(self, processor):
        result = processor._detect_file_type("Main.java")
        assert result == FILE_TYPE_CODE

    def test_detect_go_file(self, processor):
        result = processor._detect_file_type("server.go")
        assert result == FILE_TYPE_CODE

    def test_detect_rust_file(self, processor):
        result = processor._detect_file_type("main.rs")
        assert result == FILE_TYPE_CODE

    def test_detect_c_file(self, processor):
        result = processor._detect_file_type("program.c")
        assert result == FILE_TYPE_CODE

    def test_detect_cpp_file(self, processor):
        result = processor._detect_file_type("module.cpp")
        assert result == FILE_TYPE_CODE

    def test_detect_markdown_file(self, processor):
        result = processor._detect_file_type("README.md")
        assert result == FILE_TYPE_DOCUMENTATION

    def test_detect_rst_file(self, processor):
        result = processor._detect_file_type("docs.rst")
        assert result == FILE_TYPE_DOCUMENTATION

    def test_detect_txt_file(self, processor):
        result = processor._detect_file_type("notes.txt")
        assert result == FILE_TYPE_DOCUMENTATION

    def test_detect_json_file(self, processor):
        result = processor._detect_file_type("config.json")
        assert result == FILE_TYPE_CODE

    def test_detect_yaml_file(self, processor):
        result = processor._detect_file_type("settings.yaml")
        assert result == FILE_TYPE_CODE

    def test_detect_unknown_extension(self, processor):
        result = processor._detect_file_type("data.xyz")
        assert result == FILE_TYPE_OTHER

    def test_detect_no_extension(self, processor):
        result = processor._detect_file_type("Makefile")
        assert result == FILE_TYPE_OTHER

    def test_detect_uppercase_extension(self, processor):
        result = processor._detect_file_type("SCRIPT.PY")
        assert result == FILE_TYPE_CODE

    def test_detect_mixed_case_extension(self, processor):
        result = processor._detect_file_type("ReadMe.Md")
        assert result == FILE_TYPE_DOCUMENTATION

    def test_detect_path_with_dots(self, processor):
        result = processor._detect_file_type("src/utils/helper.py")
        assert result == FILE_TYPE_CODE


class TestCollectTreeInfo:
    """Test cases for _collect_tree_info() method."""

    @pytest.mark.asyncio
    async def test_collect_empty_directory(self, processor, fake_fs, ctx):
        fake_fs.set_tree({"viking://temp/empty": []})
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/empty")

        assert result == {"viking://temp/empty": ([], [])}

    @pytest.mark.asyncio
    async def test_collect_directory_with_files(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/dir": [
                    {"name": "file1.txt", "isDir": False},
                    {"name": "file2.py", "isDir": False},
                ]
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/dir")

        assert "viking://temp/dir" in result
        sub_dirs, files = result["viking://temp/dir"]
        assert sub_dirs == []
        assert len(files) == 2
        assert "viking://temp/dir/file1.txt" in files
        assert "viking://temp/dir/file2.py" in files

    @pytest.mark.asyncio
    async def test_collect_directory_with_subdirs(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/root": [
                    {"name": "subdir1", "isDir": True},
                    {"name": "subdir2", "isDir": True},
                ],
                "viking://temp/root/subdir1": [
                    {"name": "file1.txt", "isDir": False},
                ],
                "viking://temp/root/subdir2": [
                    {"name": "file2.txt", "isDir": False},
                ],
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/root")

        assert "viking://temp/root" in result
        assert "viking://temp/root/subdir1" in result
        assert "viking://temp/root/subdir2" in result

    @pytest.mark.asyncio
    async def test_collect_nested_directories(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/root": [
                    {"name": "level1", "isDir": True},
                ],
                "viking://temp/root/level1": [
                    {"name": "level2", "isDir": True},
                ],
                "viking://temp/root/level1/level2": [
                    {"name": "deep_file.txt", "isDir": False},
                ],
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/root")

        assert "viking://temp/root" in result
        assert "viking://temp/root/level1" in result
        assert "viking://temp/root/level1/level2" in result

    @pytest.mark.asyncio
    async def test_collect_skips_hidden_files(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/dir": [
                    {"name": ".hidden", "isDir": False},
                    {"name": "visible.txt", "isDir": False},
                ]
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/dir")

        _, files = result["viking://temp/dir"]
        assert len(files) == 1
        assert "viking://temp/dir/visible.txt" in files

    @pytest.mark.asyncio
    async def test_collect_skips_dot_and_dotdot(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/dir": [
                    {"name": ".", "isDir": True},
                    {"name": "..", "isDir": True},
                    {"name": "file.txt", "isDir": False},
                ]
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/dir")

        sub_dirs, files = result["viking://temp/dir"]
        assert sub_dirs == []
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_collect_handles_ls_error(self, processor, fake_fs, ctx):
        fake_fs.ls = AsyncMock(side_effect=Exception("LS error"))
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._collect_tree_info("viking://temp/dir")

        assert result == {}


class TestComputeDiff:
    """Test cases for _compute_diff() method."""

    @pytest.mark.asyncio
    async def test_compute_diff_no_changes(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": ([], ["viking://temp/root/file.txt"]),
        }
        target_tree = {
            "viking://target/root": ([], ["viking://target/root/file.txt"]),
        }
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert diff.added_files == []
        assert diff.deleted_files == []
        assert diff.updated_files == []
        assert diff.added_dirs == []
        assert diff.deleted_dirs == []

    @pytest.mark.asyncio
    async def test_compute_diff_added_files(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": ([], ["viking://temp/root/new_file.txt"]),
        }
        target_tree = {
            "viking://target/root": ([], []),
        }
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert len(diff.added_files) == 1
        assert "viking://temp/root/new_file.txt" in diff.added_files
        assert diff.deleted_files == []

    @pytest.mark.asyncio
    async def test_compute_diff_deleted_files(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": ([], []),
        }
        target_tree = {
            "viking://target/root": ([], ["viking://target/root/old_file.txt"]),
        }
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert diff.added_files == []
        assert len(diff.deleted_files) == 1
        assert "viking://target/root/old_file.txt" in diff.deleted_files

    @pytest.mark.asyncio
    async def test_compute_diff_updated_files(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": ([], ["viking://temp/root/file.txt"]),
        }
        target_tree = {
            "viking://target/root": ([], ["viking://target/root/file.txt"]),
        }
        fake_fs.set_file_contents(
            {
                "viking://temp/root/file.txt": "new content",
                "viking://target/root/file.txt": "old content",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert len(diff.updated_files) == 1
        assert "viking://temp/root/file.txt" in diff.updated_files

    @pytest.mark.asyncio
    async def test_compute_diff_unchanged_files(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": ([], ["viking://temp/root/file.txt"]),
        }
        target_tree = {
            "viking://target/root": ([], ["viking://target/root/file.txt"]),
        }
        fake_fs.set_file_contents(
            {
                "viking://temp/root/file.txt": "same content",
                "viking://target/root/file.txt": "same content",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert diff.updated_files == []

    @pytest.mark.asyncio
    async def test_compute_diff_added_dirs(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": (["viking://temp/root/new_dir"], []),
            "viking://temp/root/new_dir": ([], []),
        }
        target_tree = {
            "viking://target/root": ([], []),
        }
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert len(diff.added_dirs) == 1
        assert "viking://temp/root/new_dir" in diff.added_dirs

    @pytest.mark.asyncio
    async def test_compute_diff_deleted_dirs(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": ([], []),
        }
        target_tree = {
            "viking://target/root": (["viking://target/root/old_dir"], []),
            "viking://target/root/old_dir": ([], []),
        }
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert len(diff.deleted_dirs) == 1
        assert "viking://target/root/old_dir" in diff.deleted_dirs

    @pytest.mark.asyncio
    async def test_compute_diff_mixed_changes(self, processor, fake_fs, ctx):
        root_tree = {
            "viking://temp/root": (
                ["viking://temp/root/new_dir"],
                ["viking://temp/root/new_file.txt", "viking://temp/root/updated.txt"],
            ),
            "viking://temp/root/new_dir": ([], []),
        }
        target_tree = {
            "viking://target/root": (
                ["viking://target/root/old_dir"],
                ["viking://target/root/updated.txt", "viking://target/root/deleted.txt"],
            ),
            "viking://target/root/old_dir": ([], []),
        }
        fake_fs.set_file_contents(
            {
                "viking://temp/root/updated.txt": "new content",
                "viking://target/root/updated.txt": "old content",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            diff = await processor._compute_diff(
                root_tree, target_tree, "viking://temp/root", "viking://target/root"
            )

        assert len(diff.added_files) == 1
        assert len(diff.deleted_files) == 1
        assert len(diff.updated_files) == 1
        assert len(diff.added_dirs) == 1
        assert len(diff.deleted_dirs) == 1


class TestCheckFileContentChanged:
    """Test cases for _check_file_content_changed() method."""

    @pytest.mark.asyncio
    async def test_content_changed(self, processor, fake_fs, ctx):
        fake_fs.set_file_contents(
            {
                "viking://temp/file.txt": "new content",
                "viking://target/file.txt": "old content",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._check_file_content_changed(
                "viking://temp/file.txt", "viking://target/file.txt"
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_content_unchanged(self, processor, fake_fs, ctx):
        fake_fs.set_file_contents(
            {
                "viking://temp/file.txt": "same content",
                "viking://target/file.txt": "same content",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._check_file_content_changed(
                "viking://temp/file.txt", "viking://target/file.txt"
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_content_changed_empty_files(self, processor, fake_fs, ctx):
        fake_fs.set_file_contents(
            {
                "viking://temp/file.txt": "",
                "viking://target/file.txt": "",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._check_file_content_changed(
                "viking://temp/file.txt", "viking://target/file.txt"
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_content_changed_one_empty(self, processor, fake_fs, ctx):
        fake_fs.set_file_contents(
            {
                "viking://temp/file.txt": "content",
                "viking://target/file.txt": "",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._check_file_content_changed(
                "viking://temp/file.txt", "viking://target/file.txt"
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_content_changed_on_exception(self, processor, fake_fs, ctx):
        fake_fs.read_file = AsyncMock(side_effect=Exception("Read error"))
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            result = await processor._check_file_content_changed(
                "viking://temp/file.txt", "viking://target/file.txt"
            )

        assert result is True


class TestExecuteSyncOperations:
    """Test cases for _execute_sync_operations() method."""

    @pytest.mark.asyncio
    async def test_execute_delete_files(self, processor, fake_fs, ctx):
        diff = DiffResult(
            added_files=[],
            deleted_files=["viking://target/deleted.txt"],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=[],
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            await processor._execute_sync_operations(
                diff, "viking://temp/root", "viking://target/root"
            )

        assert "viking://target/deleted.txt" in fake_fs.deleted_files

    @pytest.mark.asyncio
    async def test_execute_move_added_files(self, processor, fake_fs, ctx):
        diff = DiffResult(
            added_files=["viking://temp/root/new.txt"],
            deleted_files=[],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=[],
        )
        processor._current_ctx = ctx

        mock_viking_uri = MagicMock()
        mock_viking_uri.parent = None

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            with patch(
                "openviking.storage.queuefs.semantic_processor.VikingURI",
                return_value=mock_viking_uri,
            ):
                await processor._execute_sync_operations(
                    diff, "viking://temp/root", "viking://target/root"
                )

        assert ("viking://temp/root/new.txt", "viking://target/root/new.txt") in fake_fs.moved_files

    @pytest.mark.asyncio
    async def test_execute_move_updated_files(self, processor, fake_fs, ctx):
        diff = DiffResult(
            added_files=[],
            deleted_files=[],
            updated_files=["viking://temp/root/updated.txt"],
            added_dirs=[],
            deleted_dirs=[],
        )
        processor._current_ctx = ctx

        mock_viking_uri = MagicMock()
        mock_viking_uri.parent = None

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            with patch(
                "openviking.storage.queuefs.semantic_processor.VikingURI",
                return_value=mock_viking_uri,
            ):
                await processor._execute_sync_operations(
                    diff, "viking://temp/root", "viking://target/root"
                )

        assert "viking://target/root/updated.txt" in fake_fs.deleted_files
        assert (
            "viking://temp/root/updated.txt",
            "viking://target/root/updated.txt",
        ) in fake_fs.moved_files

    @pytest.mark.asyncio
    async def test_execute_delete_dirs(self, processor, fake_fs, ctx):
        diff = DiffResult(
            added_files=[],
            deleted_files=[],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=["viking://target/root/old_dir"],
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            await processor._execute_sync_operations(
                diff, "viking://temp/root", "viking://target/root"
            )

        assert "viking://target/root/old_dir" in fake_fs.deleted_dirs

    @pytest.mark.asyncio
    async def test_execute_delete_dirs_deepest_first(self, processor, fake_fs, ctx):
        diff = DiffResult(
            added_files=[],
            deleted_files=[],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=[
                "viking://target/root/level1",
                "viking://target/root/level1/level2",
                "viking://target/root/level1/level2/level3",
            ],
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            await processor._execute_sync_operations(
                diff, "viking://temp/root", "viking://target/root"
            )

        assert len(fake_fs.deleted_dirs) == 3
        deepest_first = sorted(fake_fs.deleted_dirs, key=lambda x: x.count("/"), reverse=True)
        assert fake_fs.deleted_dirs == deepest_first

    @pytest.mark.asyncio
    async def test_execute_creates_parent_dirs(self, processor, fake_fs, ctx):
        diff = DiffResult(
            added_files=["viking://temp/root/subdir/new.txt"],
            deleted_files=[],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=[],
        )
        processor._current_ctx = ctx

        mock_parent = MagicMock()
        mock_parent.uri = "viking://target/root/subdir"
        mock_viking_uri = MagicMock()
        mock_viking_uri.parent = mock_parent

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            with patch(
                "openviking.storage.queuefs.semantic_processor.VikingURI",
                return_value=mock_viking_uri,
            ):
                await processor._execute_sync_operations(
                    diff, "viking://temp/root", "viking://target/root"
                )

        assert "viking://target/root/subdir" in fake_fs.created_dirs


class TestCreateSyncDiffCallback:
    """Test cases for _create_sync_diff_callback() method."""

    @pytest.mark.asyncio
    async def test_callback_returns_callable(self, processor):
        callback = processor._create_sync_diff_callback(
            "viking://temp/root", "viking://target/root"
        )
        assert callable(callback)

    @pytest.mark.asyncio
    async def test_callback_is_async(self, processor):
        callback = processor._create_sync_diff_callback(
            "viking://temp/root", "viking://target/root"
        )
        import asyncio

        assert asyncio.iscoroutinefunction(callback)

    @pytest.mark.asyncio
    async def test_callback_collects_tree_info(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/root": [
                    {"name": "file.txt", "isDir": False},
                ],
                "viking://target/root": [
                    {"name": "file.txt", "isDir": False},
                ],
            }
        )
        fake_fs.set_file_contents(
            {
                "viking://temp/root/file.txt": "content",
                "viking://target/root/file.txt": "content",
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            callback = processor._create_sync_diff_callback(
                "viking://temp/root", "viking://target/root"
            )
            await callback()

    @pytest.mark.asyncio
    async def test_callback_handles_exception(self, processor, fake_fs, ctx):
        fake_fs.ls = AsyncMock(side_effect=Exception("Test error"))
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            callback = processor._create_sync_diff_callback(
                "viking://temp/root", "viking://target/root"
            )
            await callback()

    @pytest.mark.asyncio
    async def test_callback_deletes_root_after_sync(self, processor, fake_fs, ctx):
        fake_fs.set_tree(
            {
                "viking://temp/root": [],
                "viking://target/root": [],
            }
        )
        processor._current_ctx = ctx

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fake_fs
        ):
            callback = processor._create_sync_diff_callback(
                "viking://temp/root", "viking://target/root"
            )
            await callback()

        assert "viking://temp/root" in fake_fs.deleted_dirs


class TestDiffResult:
    """Test cases for DiffResult dataclass."""

    def test_diff_result_default_values(self):
        diff = DiffResult()
        assert diff.added_files == []
        assert diff.deleted_files == []
        assert diff.updated_files == []
        assert diff.added_dirs == []
        assert diff.deleted_dirs == []

    def test_diff_result_with_values(self):
        diff = DiffResult(
            added_files=["a.txt"],
            deleted_files=["b.txt"],
            updated_files=["c.txt"],
            added_dirs=["dir1"],
            deleted_dirs=["dir2"],
        )
        assert diff.added_files == ["a.txt"]
        assert diff.deleted_files == ["b.txt"]
        assert diff.updated_files == ["c.txt"]
        assert diff.added_dirs == ["dir1"]
        assert diff.deleted_dirs == ["dir2"]

    def test_diff_result_modifiable(self):
        diff = DiffResult()
        diff.added_files.append("new.txt")
        assert "new.txt" in diff.added_files
