# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils.embedding_utils import index_resource
from openviking_cli.session.user_id import UserIdentifier


class _DummyVikingFS:
    def __init__(self, entries_by_uri, files_by_uri):
        self.entries_by_uri = entries_by_uri
        self.files_by_uri = files_by_uri

    async def exists(self, uri, ctx=None):
        return uri in self.files_by_uri

    async def read_file(self, uri, ctx=None):
        return self.files_by_uri[uri]

    async def ls(self, uri, ctx=None):
        return self.entries_by_uri.get(uri, [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recursive", "expected_dirs", "expected_files"),
    [
        (
            False,
            ["viking://resources/root"],
            [("viking://resources/root/top.md", "viking://resources/root")],
        ),
        (
            True,
            [
                "viking://resources/root",
                "viking://resources/root/nested",
            ],
            [
                ("viking://resources/root/top.md", "viking://resources/root"),
                ("viking://resources/root/nested/child.md", "viking://resources/root/nested"),
            ],
        ),
    ],
)
async def test_index_resource_recursive_traversal(recursive, expected_dirs, expected_files):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    vfs = _DummyVikingFS(
        entries_by_uri={
            "viking://resources/root": [
                {"name": ".abstract.md", "uri": "viking://resources/root/.abstract.md", "isDir": False},
                {"name": "top.md", "uri": "viking://resources/root/top.md", "isDir": False},
                {"name": "nested", "uri": "viking://resources/root/nested", "isDir": True},
            ],
            "viking://resources/root/nested": [
                {
                    "name": ".abstract.md",
                    "uri": "viking://resources/root/nested/.abstract.md",
                    "isDir": False,
                },
                {
                    "name": "child.md",
                    "uri": "viking://resources/root/nested/child.md",
                    "isDir": False,
                },
            ],
        },
        files_by_uri={
            "viking://resources/root/.abstract.md": b"root abstract",
            "viking://resources/root/nested/.abstract.md": b"nested abstract",
        },
    )
    seen_dirs = []
    seen_files = []

    async def fake_vectorize_directory_meta(uri, abstract, overview, context_type, ctx):
        seen_dirs.append(uri)

    async def fake_vectorize_file(file_path, summary_dict, parent_uri, context_type, ctx):
        seen_files.append((file_path, parent_uri))

    with (
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=vfs),
        patch(
            "openviking.utils.embedding_utils.vectorize_directory_meta",
            side_effect=fake_vectorize_directory_meta,
        ),
        patch("openviking.utils.embedding_utils.vectorize_file", side_effect=fake_vectorize_file),
    ):
        await index_resource("viking://resources/root", ctx, recursive=recursive)

    assert seen_dirs == expected_dirs
    assert seen_files == expected_files
