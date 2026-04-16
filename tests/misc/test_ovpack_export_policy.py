# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for ovpack export filtering of derived semantic files."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.local_fs import export_ovpack
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self) -> None:
        self.entries = [
            {"rel_path": ".meta.json", "name": ".meta.json", "isDir": False, "size": 2},
            {"rel_path": ".abstract.md", "name": ".abstract.md", "isDir": False, "size": 8},
            {"rel_path": ".overview.md", "name": ".overview.md", "isDir": False, "size": 8},
            {"rel_path": ".relations.json", "name": ".relations.json", "isDir": False, "size": 8},
            {"rel_path": "keep.md", "name": "keep.md", "isDir": False, "size": 4},
            {"rel_path": "nested", "name": "nested", "isDir": True},
            {"rel_path": "nested/info.txt", "name": "info.txt", "isDir": False, "size": 4},
            {
                "rel_path": "nested/.overview.md",
                "name": ".overview.md",
                "isDir": False,
                "size": 8,
            },
        ]
        self.file_map = {
            "viking://resources/demo/.meta.json": b"{}",
            "viking://resources/demo/.abstract.md": b"abstract",
            "viking://resources/demo/.overview.md": b"overview",
            "viking://resources/demo/.relations.json": b'{"a":1}',
            "viking://resources/demo/keep.md": b"keep",
            "viking://resources/demo/nested/info.txt": b"info",
            "viking://resources/demo/nested/.overview.md": b"nested overview",
        }

    async def tree(self, uri: str, show_all_hidden: bool = True, ctx=None):
        return self.entries

    async def read_file_bytes(self, uri: str, ctx=None):
        return self.file_map[uri]


@pytest.fixture
def request_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice", "agent1"), role=Role.USER)


@pytest.mark.asyncio
async def test_export_ovpack_skips_derived_semantic_files(
    tmp_path: Path, request_ctx: RequestContext
):
    export_path = tmp_path / "demo.ovpack"

    await export_ovpack(
        FakeVikingFS(),
        "viking://resources/demo",
        str(export_path),
        ctx=request_ctx,
    )

    with zipfile.ZipFile(export_path, "r") as zf:
        names = set(zf.namelist())

    assert "demo/" in names
    assert "demo/_._meta.json" in names
    assert "demo/keep.md" in names
    assert "demo/nested/" in names
    assert "demo/nested/info.txt" in names
    assert "demo/_._abstract.md" not in names
    assert "demo/_._overview.md" not in names
    assert "demo/_._relations.json" not in names
    assert "demo/nested/_._overview.md" not in names
