# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local-artifact-mode parsing for CodeRepositoryParser.

When a parse_output_store is threaded through parse kwargs (local mode), the
repository parser must write its artifacts into that store and never touch the
VikingFS singleton (AGFS temp). Without a store it keeps the legacy AGFS path.
"""

from pathlib import Path

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.code.code import CodeRepositoryParser


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    (repo / "README.md").write_text("# demo", encoding="utf-8")
    # Minimal git marker so DirectoryParser-style detection is consistent; the
    # parser itself accepts a plain local dir.
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    return repo


class _ExplodingVikingFS:
    """Any write here means the local path leaked back to AGFS temp."""

    def create_temp_uri(self, ctx=None):
        raise AssertionError("local mode must not allocate an AGFS temp uri")

    async def write_file_bytes(self, uri, content, **kwargs):
        raise AssertionError("local mode must not write VikingFS")

    async def mkdir(self, uri, exist_ok=False, **kwargs):
        raise AssertionError("local mode must not mkdir on VikingFS")


@pytest.mark.asyncio
async def test_code_parse_writes_to_local_store(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))

    parser = CodeRepositoryParser()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: _ExplodingVikingFS())

    result = await parser.parse(
        str(repo),
        _source_meta={"repo_name": "acme/demo"},
        parse_output_store=store,
    )

    # Artifact ref points at the local backend; temp_dir_path is the local root.
    assert result.artifact_ref is not None
    assert result.artifact_ref.backend == "local"
    assert result.temp_dir_path == result.artifact_ref.root

    # Files landed in the store under repository/... and match source bytes.
    ref = result.artifact_ref
    names = {e.rel_path for e in await store.list(ref, "repository")}
    assert "repository/README.md" in names
    assert await store.read_bytes(ref, "repository/src/main.py") == b"print('hi')"


@pytest.mark.asyncio
async def test_code_parse_without_store_uses_agfs(tmp_path):
    # No store supplied -> legacy AGFS path. We only assert it does not raise the
    # local-mode branch and yields an AGFS-shaped temp_dir_path via a fake fs.
    repo = _make_repo(tmp_path)

    class _FakeAgfs:
        def __init__(self):
            self.files = {}
            self._n = 0

        def create_temp_uri(self, ctx=None):
            self._n += 1
            return f"viking://temp/code{self._n}"

        async def mkdir(self, uri, exist_ok=False, **kwargs):
            pass

        async def write_file_bytes(self, uri, content, **kwargs):
            self.files[uri] = content

    parser = CodeRepositoryParser()
    fake = _FakeAgfs()
    parser._get_viking_fs = lambda: fake  # type: ignore[method-assign]

    result = await parser.parse(str(repo), _source_meta={"repo_name": "acme/demo"})

    assert result.artifact_ref is None or result.artifact_ref.backend == "agfs"
    assert result.temp_dir_path.startswith("viking://temp/")
    assert any("/repository/" in uri for uri in fake.files)
