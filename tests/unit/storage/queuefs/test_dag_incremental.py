# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for SemanticDagExecutor incremental update and content change detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def mock_processor():
    """Create a mock SemanticProcessor."""
    processor = MagicMock()
    processor._generate_single_file_summary = AsyncMock(
        return_value={"name": "test.py", "summary": "test summary"}
    )
    processor._generate_overview = AsyncMock(return_value="test overview")
    processor._extract_abstract_from_overview = MagicMock(return_value="test abstract")
    processor._vectorize_single_file = AsyncMock()
    processor._vectorize_directory = AsyncMock()
    return processor


@pytest.fixture
def mock_viking_fs():
    """Create a mock VikingFS."""
    fs = MagicMock()
    fs.ls = AsyncMock(return_value=[])
    fs.read_file = AsyncMock(return_value="")
    fs.write_file = AsyncMock()
    fs._get_vector_store = MagicMock(return_value=None)
    return fs


@pytest.fixture
def mock_vector_store():
    """Create a mock VectorStore."""
    store = MagicMock()
    store.get_context_by_uri = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_context():
    """Create a mock RequestContext."""
    user = MagicMock(spec=UserIdentifier)
    user.account_id = "test_account"
    user.user_id = "test_user"
    return RequestContext(user=user, role=Role.USER)


@pytest.fixture
def executor(mock_processor, mock_context, mock_viking_fs):
    """Create a SemanticDagExecutor instance for testing."""
    with patch(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs", return_value=mock_viking_fs
    ):
        executor = SemanticDagExecutor(
            processor=mock_processor,
            context_type="resource",
            max_concurrent_llm=5,
            ctx=mock_context,
            incremental_update=True,
            target_uri="viking://resource/target",
            recursive=True,
        )
    return executor


class TestGetTargetFilePath:
    """Tests for _get_target_file_path() method."""

    def test_returns_none_when_incremental_update_disabled(
        self, mock_processor, mock_context, mock_viking_fs
    ):
        with patch(
            "openviking.storage.queuefs.semantic_dag.get_viking_fs", return_value=mock_viking_fs
        ):
            executor = SemanticDagExecutor(
                processor=mock_processor,
                context_type="resource",
                max_concurrent_llm=5,
                ctx=mock_context,
                incremental_update=False,
                target_uri="viking://resource/target",
            )
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/root/file.py")
        assert result is None

    def test_returns_none_when_target_uri_is_none(
        self, mock_processor, mock_context, mock_viking_fs
    ):
        with patch(
            "openviking.storage.queuefs.semantic_dag.get_viking_fs", return_value=mock_viking_fs
        ):
            executor = SemanticDagExecutor(
                processor=mock_processor,
                context_type="resource",
                max_concurrent_llm=5,
                ctx=mock_context,
                incremental_update=True,
                target_uri=None,
            )
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/root/file.py")
        assert result is None

    def test_returns_none_when_root_uri_is_none(self, executor):
        executor._root_uri = None
        result = executor._get_target_file_path("viking://resource/root/file.py")
        assert result is None

    def test_returns_target_path_for_file_in_root(self, executor):
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/root/file.py")
        assert result == "viking://resource/target/file.py"

    def test_returns_target_path_for_file_in_subdirectory(self, executor):
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/root/subdir/file.py")
        assert result == "viking://resource/target/subdir/file.py"

    def test_returns_target_uri_when_current_uri_equals_root(self, executor):
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/root")
        assert result == "viking://resource/target"

    def test_handles_nested_paths(self, executor):
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/root/a/b/c/file.py")
        assert result == "viking://resource/target/a/b/c/file.py"

    def test_handles_path_prefix_matching(self, executor):
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path("viking://resource/rootdir/file.py")
        assert result == "viking://resource/target/dir/file.py"

    def test_returns_none_on_exception(self, executor):
        executor._root_uri = "viking://resource/root"
        result = executor._get_target_file_path(None)
        assert result is None


class TestCheckFileContentChanged:
    """Tests for _check_file_content_changed() method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_target_path_is_none(self, executor):
        executor._root_uri = None
        result = await executor._check_file_content_changed("viking://resource/root/file.py")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_content_differs(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(side_effect=["current content", "target content"])

        result = await executor._check_file_content_changed("viking://resource/root/file.py")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_content_identical(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(return_value="same content")

        result = await executor._check_file_content_changed("viking://resource/root/file.py")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_read_exception(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(side_effect=Exception("read error"))

        result = await executor._check_file_content_changed("viking://resource/root/file.py")
        assert result is True

    @pytest.mark.asyncio
    async def test_calls_read_file_with_correct_paths(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(return_value="content")

        await executor._check_file_content_changed("viking://resource/root/subdir/file.py")

        assert mock_viking_fs.read_file.call_count == 2
        calls = mock_viking_fs.read_file.call_args_list
        assert calls[0][0][0] == "viking://resource/root/subdir/file.py"
        assert calls[1][0][0] == "viking://resource/target/subdir/file.py"


class TestReadExistingSummary:
    """Tests for _read_existing_summary() method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_target_path_is_none(self, executor):
        executor._root_uri = None
        result = await executor._read_existing_summary("viking://resource/root/file.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_vector_store_is_none(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=None)

        result = await executor._read_existing_summary("viking://resource/root/file.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_summary_dict_when_record_exists(
        self, executor, mock_viking_fs, mock_vector_store
    ):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_vector_store.get_context_by_uri = AsyncMock(
            return_value=[{"abstract": "existing summary content"}]
        )

        result = await executor._read_existing_summary("viking://resource/root/subdir/file.py")

        assert result is not None
        assert result["name"] == "file.py"
        assert result["summary"] == "existing summary content"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_records(self, executor, mock_viking_fs, mock_vector_store):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_vector_store.get_context_by_uri = AsyncMock(return_value=[])

        result = await executor._read_existing_summary("viking://resource/root/file.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_abstract_is_empty(
        self, executor, mock_viking_fs, mock_vector_store
    ):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_vector_store.get_context_by_uri = AsyncMock(return_value=[{"abstract": ""}])

        result = await executor._read_existing_summary("viking://resource/root/file.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, executor, mock_viking_fs, mock_vector_store):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_vector_store.get_context_by_uri = AsyncMock(side_effect=Exception("db error"))

        result = await executor._read_existing_summary("viking://resource/root/file.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_vector_store_with_correct_uri(
        self, executor, mock_viking_fs, mock_vector_store, mock_context
    ):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_vector_store.get_context_by_uri = AsyncMock(return_value=[{"abstract": "summary"}])

        await executor._read_existing_summary("viking://resource/root/file.py")

        mock_vector_store.get_context_by_uri.assert_called_once_with(
            account_id=mock_context.account_id,
            uri="viking://resource/target/file.py",
            limit=1,
        )


class TestCheckDirChildrenChanged:
    """Tests for _check_dir_children_changed() method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_target_path_is_none(self, executor):
        executor._root_uri = None
        result = await executor._check_dir_children_changed(
            "viking://resource/root", ["file1.py"], ["dir1"]
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_children_identical(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        current_files = ["viking://resource/root/file1.py", "viking://resource/root/file2.py"]
        current_dirs = ["viking://resource/root/dir1", "viking://resource/root/dir2"]

        executor._list_dir = AsyncMock(
            side_effect=[
                (
                    ["viking://resource/target/dir1", "viking://resource/target/dir2"],
                    ["viking://resource/target/file1.py", "viking://resource/target/file2.py"],
                ),
                ([], []),
            ]
        )
        mock_viking_fs.read_file = AsyncMock(return_value="same content")

        result = await executor._check_dir_children_changed(
            "viking://resource/root", current_files, current_dirs
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_file_names_differ(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        current_files = ["viking://resource/root/file1.py"]
        current_dirs = []

        mock_viking_fs.ls = AsyncMock(
            return_value=[
                {"name": "file2.py", "isDir": False},
            ]
        )

        result = await executor._check_dir_children_changed(
            "viking://resource/root", current_files, current_dirs
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_dir_names_differ(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        current_files = []
        current_dirs = ["viking://resource/root/dir1"]

        mock_viking_fs.ls = AsyncMock(
            return_value=[
                {"name": "dir2", "isDir": True},
            ]
        )

        result = await executor._check_dir_children_changed(
            "viking://resource/root", current_files, current_dirs
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_file_content_changed(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        current_files = ["viking://resource/root/file1.py"]
        current_dirs = []

        mock_viking_fs.ls = AsyncMock(
            side_effect=[
                [{"name": "file1.py", "isDir": False}],
                [],
            ]
        )
        mock_viking_fs.read_file = AsyncMock(side_effect=["old content", "new content"])

        result = await executor._check_dir_children_changed(
            "viking://resource/root", current_files, current_dirs
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_exception(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.ls = AsyncMock(side_effect=Exception("ls error"))

        result = await executor._check_dir_children_changed(
            "viking://resource/root", ["file1.py"], []
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_handles_empty_directories(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.ls = AsyncMock(return_value=[])

        result = await executor._check_dir_children_changed("viking://resource/root", [], [])
        assert result is False

    @pytest.mark.asyncio
    async def test_ignores_dot_files_in_comparison(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        current_files = ["viking://resource/root/file1.py"]
        current_dirs = []

        executor._list_dir = AsyncMock(
            return_value=(
                [],
                ["viking://resource/target/file1.py"],
            )
        )

        result = await executor._check_dir_children_changed(
            "viking://resource/root", current_files, current_dirs
        )
        assert result is False


class TestReadExistingOverviewAbstract:
    """Tests for _read_existing_overview_abstract() method."""

    @pytest.mark.asyncio
    async def test_returns_none_tuple_when_target_path_is_none(self, executor):
        executor._root_uri = None
        result = await executor._read_existing_overview_abstract("viking://resource/root")
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_returns_overview_and_abstract_when_files_exist(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(side_effect=["overview content", "abstract content"])

        result = await executor._read_existing_overview_abstract("viking://resource/root/dir")

        assert result == ("overview content", "abstract content")
        calls = mock_viking_fs.read_file.call_args_list
        assert calls[0][0][0] == "viking://resource/target/dir/.overview.md"
        assert calls[1][0][0] == "viking://resource/target/dir/.abstract.md"

    @pytest.mark.asyncio
    async def test_returns_none_tuple_on_exception(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(side_effect=Exception("read error"))

        result = await executor._read_existing_overview_abstract("viking://resource/root/dir")
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_handles_missing_overview_file(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(
            side_effect=[Exception("not found"), "abstract content"]
        )

        result = await executor._read_existing_overview_abstract("viking://resource/root/dir")
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_handles_missing_abstract_file(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(
            side_effect=["overview content", Exception("not found")]
        )

        result = await executor._read_existing_overview_abstract("viking://resource/root/dir")
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_calls_read_file_with_context(self, executor, mock_viking_fs, mock_context):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(return_value="content")

        await executor._read_existing_overview_abstract("viking://resource/root/dir")

        for call in mock_viking_fs.read_file.call_args_list:
            assert "ctx" in call[1]
            assert call[1]["ctx"] == mock_context

    @pytest.mark.asyncio
    async def test_handles_nested_directory_path(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(return_value="content")

        await executor._read_existing_overview_abstract("viking://resource/root/a/b/c")

        calls = mock_viking_fs.read_file.call_args_list
        assert calls[0][0][0] == "viking://resource/target/a/b/c/.overview.md"
        assert calls[1][0][0] == "viking://resource/target/a/b/c/.abstract.md"


class TestIncrementalUpdateIntegration:
    """Integration tests for incremental update scenarios."""

    @pytest.mark.asyncio
    async def test_full_incremental_flow_no_changes(
        self, executor, mock_viking_fs, mock_vector_store
    ):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_viking_fs.ls = AsyncMock(return_value=[])
        mock_viking_fs.read_file = AsyncMock(return_value="same content")
        mock_vector_store.get_context_by_uri = AsyncMock(
            return_value=[{"abstract": "existing summary"}]
        )

        content_changed = await executor._check_file_content_changed(
            "viking://resource/root/file.py"
        )
        assert content_changed is False

        summary = await executor._read_existing_summary("viking://resource/root/file.py")
        assert summary is not None
        assert summary["summary"] == "existing summary"

    @pytest.mark.asyncio
    async def test_full_incremental_flow_with_changes(
        self, executor, mock_viking_fs, mock_vector_store
    ):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"
        mock_viking_fs._get_vector_store = MagicMock(return_value=mock_vector_store)

        mock_viking_fs.read_file = AsyncMock(side_effect=["new content", "old content"])

        content_changed = await executor._check_file_content_changed(
            "viking://resource/root/file.py"
        )
        assert content_changed is True

    @pytest.mark.asyncio
    async def test_directory_change_detection_flow(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        executor._list_dir = AsyncMock(
            side_effect=[
                ([], ["viking://resource/target/file1.py", "viking://resource/target/file2.py"]),
                ([], []),
            ]
        )
        mock_viking_fs.read_file = AsyncMock(return_value="same content")

        current_files = ["viking://resource/root/file1.py", "viking://resource/root/file2.py"]
        changed = await executor._check_dir_children_changed(
            "viking://resource/root", current_files, []
        )
        assert changed is False

    @pytest.mark.asyncio
    async def test_overview_abstract_read_flow(self, executor, mock_viking_fs):
        executor._viking_fs = mock_viking_fs
        executor._root_uri = "viking://resource/root"

        mock_viking_fs.read_file = AsyncMock(side_effect=["existing overview", "existing abstract"])

        overview, abstract = await executor._read_existing_overview_abstract(
            "viking://resource/root/subdir"
        )
        assert overview == "existing overview"
        assert abstract == "existing abstract"
