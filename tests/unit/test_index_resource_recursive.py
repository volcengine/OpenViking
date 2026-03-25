# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for recursive directory indexing in index_resource()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_index_resource_recurses_into_subdirectories():
    """index_resource traverses subdirectories when recursive=True."""
    mock_ctx = MagicMock()
    mock_fs = AsyncMock()

    # Root dir has one file and one subdir
    mock_fs.exists.return_value = False  # no .abstract.md/.overview.md
    mock_fs.ls.side_effect = [
        # Root listing
        [
            {"name": "readme.md", "type": "file"},
            {"name": "subdir", "type": "directory", "uri": "viking://test/subdir"},
        ],
        # Subdir listing
        [
            {"name": "nested.md", "type": "file"},
        ],
    ]

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=mock_fs),
        patch("openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock) as mock_vf,
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://test/", ctx=mock_ctx, recursive=True)

        # Should have vectorized both files (root + subdir)
        assert mock_vf.call_count == 2
        vectorized_paths = [
            call.kwargs.get("file_path", call.args[0] if call.args else "")
            for call in mock_vf.call_args_list
        ]
        # Check that paths include files from both levels
        assert any("readme" in str(p) for p in vectorized_paths)
        assert any("nested" in str(p) for p in vectorized_paths)


@pytest.mark.asyncio
async def test_index_resource_skips_subdirs_when_not_recursive():
    """index_resource skips subdirectories when recursive=False."""
    mock_ctx = MagicMock()
    mock_fs = AsyncMock()

    mock_fs.exists.return_value = False
    mock_fs.ls.return_value = [
        {"name": "readme.md", "type": "file"},
        {"name": "subdir", "type": "directory"},
    ]

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=mock_fs),
        patch("openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock) as mock_vf,
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://test/", ctx=mock_ctx, recursive=False)

        # Should only vectorize the file, not recurse into subdir
        assert mock_vf.call_count == 1


@pytest.mark.asyncio
async def test_index_resource_respects_max_depth():
    """index_resource stops recursing at max_depth."""
    mock_ctx = MagicMock()
    mock_fs = AsyncMock()

    mock_fs.exists.return_value = False
    # Each directory contains one subdir - infinite nesting
    mock_fs.ls.return_value = [
        {"name": "deep", "type": "directory", "uri": "viking://test/deep"},
    ]

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=mock_fs),
        patch("openviking.utils.embedding_utils.vectorize_file", new_callable=AsyncMock),
        patch("openviking.utils.embedding_utils.vectorize_directory_meta", new_callable=AsyncMock),
    ):
        from openviking.utils.embedding_utils import index_resource

        await index_resource("viking://test/", ctx=mock_ctx, recursive=True, max_depth=2)

        # ls should be called for depth 0, 1, and 2 (3 calls total), then stop
        assert mock_fs.ls.call_count == 3
