# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for full-artifact upload (initial import = the plan is all added)."""

import pytest

from openviking.parse.output import ParseArtifactRef
from openviking.storage.resource_diff_apply import apply_full_artifact_upload
from openviking.utils.content_hash import content_md5


class _FakeStore:
    """Store keyed by (root, rel); read resolves ref.root + rel like the real one."""

    def __init__(self, files):
        self._files = dict(files)

    async def list(self, ref, rel_path=""):
        from openviking.parse.output import ArtifactEntry

        base = f"{ref.root.rstrip('/')}/{rel_path}".rstrip("/") + "/"
        seen = {}
        for stored in self._files:
            if stored.startswith(base):
                rest = stored[len(base):]
                head = rest.split("/", 1)
                name = head[0]
                is_dir = len(head) > 1
                child_rel = f"{rel_path}/{name}" if rel_path else name
                seen[name] = ArtifactEntry(name=name, rel_path=child_rel, is_dir=is_dir)
        return list(seen.values())

    async def read_bytes(self, ref, rel_path):
        return self._files[f"{ref.root.rstrip('/')}/{rel_path}"]


class _FakeTarget:
    def __init__(self):
        self.written = {}

    async def write_file(self, rel_path, data):
        self.written[rel_path] = data
        return data


@pytest.mark.asyncio
async def test_full_upload_uploads_all_files_under_doc_root() -> None:
    # Artifact root holds repository/<files>; only files under repository are
    # uploaded, and they land target-relative (repository prefix stripped).
    ref = ParseArtifactRef(backend="local", root="/tmp/art", root_type="dir")
    store = _FakeStore(
        {
            "/tmp/art/repository/a.py": b"aaa",
            "/tmp/art/repository/src/b.py": b"bbb",
        }
    )
    target = _FakeTarget()

    result = await apply_full_artifact_upload(
        store=store,
        artifact_ref=ref,
        doc_rel="repository",
        target=target,
    )

    assert target.written == {"a.py": b"aaa", "src/b.py": b"bbb"}
    assert result.md5_by_rel["a.py"] == content_md5(b"aaa")
    assert set(result.uploaded) == {"a.py", "src/b.py"}
