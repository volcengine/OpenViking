#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for TreeBuilder final URI metadata."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


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

        async def _exists(uri, **kwargs):
            return uri in existing_uris

        fs.ls = AsyncMock(side_effect=_ls)
        fs.stat = AsyncMock(side_effect=_stat)
        fs.exists = AsyncMock(side_effect=_exists)
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
    async def test_resources_root_to_flattens_single_no_split_file(self):
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
                flatten_single_file=True,
            )

        assert tree.root.uri == "viking://resources/aa.md"
        assert tree.root.temp_uri == "viking://temp/import/aa/aa.md"
        assert tree._candidate_uri == "viking://resources/aa.md"
        assert tree._root_is_file is True

    @pytest.mark.asyncio
    async def test_explicit_to_is_preserved_for_single_no_split_file(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        entries = {
            "viking://temp/import": [{"name": "aa", "isDir": True}],
            "viking://temp/import/aa": [{"name": "aa.md", "isDir": False}],
        }
        fs = self._make_fs(entries, {"viking://resources"})
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await TreeBuilder().finalize_from_temp(
                temp_dir_path="viking://temp/import",
                ctx=ctx,
                to_uri="viking://resources/custom-name",
                flatten_single_file=True,
            )

        assert tree.root.uri == "viking://resources/custom-name"
        assert tree.root.temp_uri == "viking://temp/import/aa/aa.md"
        assert tree._root_is_file is True

    @pytest.mark.asyncio
    async def test_explicit_directory_to_keeps_single_no_split_wrapper(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        entries = {
            "viking://temp/import": [{"name": "aa", "isDir": True}],
        }
        fs = self._make_fs(entries, {"viking://resources"})
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await TreeBuilder().finalize_from_temp(
                temp_dir_path="viking://temp/import",
                ctx=ctx,
                to_uri="viking://resources/custom-dir",
                flatten_single_file=False,
            )

        assert tree.root.uri == "viking://resources/custom-dir"
        assert tree.root.temp_uri == "viking://temp/import/aa"
        assert tree._root_is_file is False

    @pytest.mark.asyncio
    async def test_multiple_outputs_keep_no_split_wrapper_directory(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        entries = {
            "viking://temp/import": [{"name": "aa", "isDir": True}],
            "viking://temp/import/aa": [
                {"name": "aa.md", "isDir": False},
                {"name": "cover.png", "isDir": False},
            ],
        }
        fs = self._make_fs(entries, {"viking://resources"})
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await TreeBuilder().finalize_from_temp(
                temp_dir_path="viking://temp/import",
                ctx=ctx,
                to_uri="viking://resources",
                flatten_single_file=True,
            )

        assert tree.root.uri == "viking://resources/aa"
        assert tree.root.temp_uri == "viking://temp/import/aa"
        assert tree._root_is_file is False
