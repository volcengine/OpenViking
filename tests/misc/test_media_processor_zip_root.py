#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ZipParser single-root directory handling.

Verifies that ZipParser correctly handles:
1. ZIP with single top-level directory -> uses that directory name
2. ZIP with multiple top-level entries -> uses the extract directory
"""

import zipfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.zip_parser import ZipParser


class _FakeVikingFS:
    """Minimal in-memory VikingFS for ZipParser + DirectoryParser + TreeBuilder."""

    def __init__(self):
        self.dirs: List[str] = []
        self.files: Dict[str, bytes] = {}
        self._temp_counter = 0

    async def mkdir(self, uri: str, exist_ok: bool = False, **_: Any) -> None:
        if uri not in self.dirs:
            self.dirs.append(uri)

    async def write(self, uri: str, data: Any) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[uri] = data
        return uri

    async def write_file(self, uri: str, content: Any) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[uri] = content

    async def write_file_bytes(self, uri: str, content: bytes) -> None:
        self.files[uri] = content

    async def read(self, uri: str, offset: int = 0, size: int = -1) -> bytes:
        return self.files.get(uri, b"")

    async def ls(self, uri: str, **_: Any) -> List[Dict[str, Any]]:
        prefix = uri.rstrip("/") + "/"
        children: Dict[str, bool] = {}
        for key in list(self.files.keys()) + self.dirs:
            if key.startswith(prefix):
                rest = key[len(prefix) :]
                if not rest:
                    continue
                name = rest.split("/")[0]
                is_deeper = "/" in rest[len(name) :]
                full = f"{prefix}{name}"
                children[name] = children.get(name, False) or is_deeper or full in self.dirs
        out = []
        for name in sorted(children):
            child_uri = f"{uri.rstrip('/')}/{name}"
            out.append(
                {
                    "name": name,
                    "uri": child_uri,
                    "isDir": children[name],
                    "type": "directory" if children[name] else "file",
                }
            )
        return out

    async def stat(self, uri: str, **_: Any) -> Dict[str, Any]:
        if uri in self.dirs:
            return {"name": uri.rstrip("/").split("/")[-1], "isDir": True}
        if uri in self.files:
            return {"name": uri.rstrip("/").split("/")[-1], "isDir": False}
        raise FileNotFoundError(uri)

    async def exists(self, uri: str, **_: Any) -> bool:
        return uri in self.dirs or uri in self.files

    async def move_file(self, from_uri: str, to_uri: str) -> None:
        if from_uri in self.files:
            self.files[to_uri] = self.files.pop(from_uri)

    async def delete_temp(self, temp_uri: str) -> None:
        prefix = temp_uri.rstrip("/") + "/"
        for k in [k for k in self.files if k == temp_uri or k.startswith(prefix)]:
            del self.files[k]
        self.dirs = [d for d in self.dirs if d != temp_uri and not d.startswith(prefix)]

    def create_temp_uri(self) -> str:
        self._temp_counter += 1
        uri = f"viking://temp/ov_zip_root_test_{self._temp_counter}"
        return uri


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_uses_real_root(tmp_path: Path):
    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    parser = ZipParser()

    # Mock DirectoryParser.parse to capture what directory it's called with
    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="")

        # Verify DirectoryParser was called with the real root dir "tt_b"
        assert mock_dir_parse.called
        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name == "tt_b"


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_ignores_zip_source_name(tmp_path: Path):
    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="", source_name="tt_b.zip")

        # Verify DirectoryParser was called with the real root dir "tt_b"
        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name == "tt_b"
        # source_name should NOT be passed to DirectoryParser in this case
        assert "source_name" not in mock_dir_parse.await_args.kwargs


@pytest.mark.asyncio
async def test_zip_multiple_top_level_entries_keeps_extract_root(tmp_path: Path):
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a/readme.md", "# a\n")
        zf.writestr("b/readme.md", "# b\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="")

        # Verify DirectoryParser was called with the extract dir, not "a" or "b"
        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name != "a"
        assert called_path.name != "b"
        # Should have the ov_zip_ prefix
        assert called_path.name.startswith("ov_zip_")


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_preserves_distinct_source_name(tmp_path: Path):
    zip_path = tmp_path / "temp_upload_access.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("access-dns/readme.md", "# dns\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="", source_name="access")

        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name != "access-dns"
        assert called_path.name.startswith("ov_zip_")
        assert mock_dir_parse.await_args.kwargs.get("source_name") == "access"


@pytest.mark.asyncio
async def test_single_file_uses_source_name_for_resource_name(tmp_path: Path):
    """Test that source_name is passed through correctly when needed."""
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a/readme.md", "# a\n")
        zf.writestr("b/readme.md", "# b\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="", source_name="aa.txt")

        # Verify source_name is passed when we use the extract root
        assert mock_dir_parse.await_args.kwargs.get("source_name") == "aa.txt"


@pytest.mark.asyncio
async def test_zip_dotted_source_name_matches_dotted_root_collapses(tmp_path: Path):
    """source_name="v1.2" against a single root "v1.2" must collapse.

    Regression: ``Path("v1.2").stem`` is "v1", so a stem-only comparison
    would treat the names as different and wrap ``v1.2/`` inside another
    ``v1.2/`` layer, producing ``viking://resources/v1.2/v1.2/...``.
    """
    zip_path = tmp_path / "v1.2.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("v1.2/readme.md", "# v1.2\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="", source_name="v1.2")

        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name == "v1.2"
        assert "source_name" not in mock_dir_parse.await_args.kwargs


@pytest.mark.asyncio
async def test_zip_distinct_source_name_final_root_uri_keeps_wrapper(tmp_path: Path):
    """End-to-end: source_name="access" + zip "access-dns/..." keeps the
    outer ``access`` directory in the final ``root_uri``.

    Drives the real DirectoryParser against a fake VikingFS, then runs
    ``TreeBuilder.finalize_from_temp`` on its temp output and asserts the
    final URI is ``viking://resources/access`` with ``access-dns`` still
    living underneath it.
    """
    from openviking.parse.tree_builder import TreeBuilder
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    zip_path = tmp_path / "temp_upload_access.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("access-dns/readme.md", "# dns\n")

    fs = _FakeVikingFS()
    parser = ZipParser()

    with patch.object(BaseParser, "_get_viking_fs", return_value=fs):
        result = await parser.parse(zip_path, instruction="", source_name="access")

    assert result.temp_dir_path is not None
    # DirectoryParser wraps content under the source_name, preserving access-dns below it.
    access_files = [u for u in fs.files if "/access/access-dns/" in u]
    assert access_files, f"expected access/access-dns wrapper, got {list(fs.files)}"

    builder = TreeBuilder()
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
        tree = await builder.finalize_from_temp(
            temp_dir_path=result.temp_dir_path,
            ctx=ctx,
            scope="resources",
        )

    assert tree.root.uri == "viking://resources/access"
    assert tree.root.temp_uri.endswith("/access")


@pytest.mark.asyncio
async def test_zip_matching_source_name_final_root_uri_collapses(tmp_path: Path):
    """End-to-end: source_name="tt_b.zip" + zip "tt_b/..." collapses to
    ``viking://resources/tt_b`` (no doubled wrapper)."""
    from openviking.parse.tree_builder import TreeBuilder
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    fs = _FakeVikingFS()
    parser = ZipParser()

    with patch.object(BaseParser, "_get_viking_fs", return_value=fs):
        result = await parser.parse(zip_path, instruction="", source_name="tt_b.zip")

    assert result.temp_dir_path is not None
    # No doubled "tt_b/tt_b" wrapper under the temp root.
    assert not any("/tt_b/tt_b/" in u for u in fs.files), list(fs.files)

    builder = TreeBuilder()
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
        tree = await builder.finalize_from_temp(
            temp_dir_path=result.temp_dir_path,
            ctx=ctx,
            scope="resources",
        )

    assert tree.root.uri == "viking://resources/tt_b"
