#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for TreeBuilder._resolve_unique_uri — duplicate filename auto-rename."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _make_viking_fs_mock(existing_uris: set[str]):
    """Create a mock VikingFS whose stat() raises for non-existing URIs."""
    fs = MagicMock()

    async def _stat(uri, **kwargs):
        if uri in existing_uris:
            return {"name": uri.split("/")[-1], "isDir": True}
        raise FileNotFoundError(f"Not found: {uri}")

    fs.stat = AsyncMock(side_effect=_stat)
    return fs


class TestResolveUniqueUri:
    @pytest.mark.asyncio
    async def test_no_conflict(self):
        """When the URI is free, return it unchanged."""
        from openviking.parse.tree_builder import TreeBuilder

        fs = _make_viking_fs_mock(set())
        builder = TreeBuilder()

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            result = await builder._resolve_unique_uri("viking://resources/report")

        assert result == "viking://resources/report"

    @pytest.mark.asyncio
    async def test_single_conflict(self):
        """When base name exists, should return name_1."""
        from openviking.parse.tree_builder import TreeBuilder

        existing = {"viking://resources/report"}
        fs = _make_viking_fs_mock(existing)
        builder = TreeBuilder()

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            result = await builder._resolve_unique_uri("viking://resources/report")

        assert result == "viking://resources/report_1"

    @pytest.mark.asyncio
    async def test_multiple_conflicts(self):
        """When _1 and _2 also exist, should return _3."""
        from openviking.parse.tree_builder import TreeBuilder

        existing = {
            "viking://resources/report",
            "viking://resources/report_1",
            "viking://resources/report_2",
        }
        fs = _make_viking_fs_mock(existing)
        builder = TreeBuilder()

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            result = await builder._resolve_unique_uri("viking://resources/report")

        assert result == "viking://resources/report_3"

    @pytest.mark.asyncio
    async def test_max_attempts_exceeded(self):
        """When all candidate names are taken, raise FileExistsError."""
        from openviking.parse.tree_builder import TreeBuilder

        existing = {"viking://resources/report"} | {
            f"viking://resources/report_{i}" for i in range(1, 6)
        }
        fs = _make_viking_fs_mock(existing)
        builder = TreeBuilder()

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            with pytest.raises(FileExistsError, match="Cannot resolve unique name"):
                await builder._resolve_unique_uri("viking://resources/report", max_attempts=5)

    @pytest.mark.asyncio
    async def test_gap_in_sequence(self):
        """If _1 exists but _2 does not, should return _2 (not skip to _3)."""
        from openviking.parse.tree_builder import TreeBuilder

        existing = {
            "viking://resources/report",
            "viking://resources/report_1",
        }
        fs = _make_viking_fs_mock(existing)
        builder = TreeBuilder()

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            result = await builder._resolve_unique_uri("viking://resources/report")

        assert result == "viking://resources/report_2"


class TestFinalizeFromTemp:
    @staticmethod
    def _make_fs(entries, existing_uris: set[str]):
        fs = MagicMock()

        async def _ls(uri, **kwargs):
            return entries[uri]

        async def _stat(uri, **kwargs):
            if uri in existing_uris:
                return {"name": uri.split("/")[-1], "isDir": True}
            raise FileNotFoundError(f"Not found: {uri}")

        fs.ls = AsyncMock(side_effect=_ls)
        fs.stat = AsyncMock(side_effect=_stat)
        return fs

    @pytest.mark.asyncio
    async def test_resources_root_to_behaves_like_parent(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        entries = {
            "viking://temp/import": [{"name": "tt_b", "isDir": True}],
        }
        fs = self._make_fs(entries, {"viking://resources"})
        builder = TreeBuilder()
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path="viking://temp/import",
                ctx=ctx,
                scope="resources",
                to_uri="viking://resources",
            )

        assert tree.root.uri == "viking://resources/tt_b"
        assert tree.root.temp_uri == "viking://temp/import/tt_b"
        assert tree._candidate_uri == "viking://resources/tt_b"

    @pytest.mark.asyncio
    async def test_resources_root_to_with_trailing_slash_uses_child_incremental_target(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        entries = {
            "viking://temp/import": [{"name": "tt_b", "isDir": True}],
        }
        fs = self._make_fs(entries, {"viking://resources", "viking://resources/tt_b"})
        builder = TreeBuilder()
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path="viking://temp/import",
                ctx=ctx,
                scope="resources",
                to_uri="viking://resources/",
            )

        assert tree.root.uri == "viking://resources/tt_b"
        assert tree.root.temp_uri == "viking://temp/import/tt_b"
        assert tree._candidate_uri == "viking://resources/tt_b"

    @pytest.mark.asyncio
    async def test_resources_root_to_keeps_single_file_wrapper_directory(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        entries = {
            "viking://temp/import": [{"name": "aa", "isDir": True}],
            "viking://temp/import/aa": [{"name": "aa.md", "isDir": False}],
        }
        fs = self._make_fs(entries, {"viking://resources"})
        builder = TreeBuilder()
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path="viking://temp/import",
                ctx=ctx,
                scope="resources",
                to_uri="viking://resources",
            )

        assert tree.root.uri == "viking://resources/aa"
        assert tree.root.temp_uri == "viking://temp/import/aa"
        assert tree._candidate_uri == "viking://resources/aa"
