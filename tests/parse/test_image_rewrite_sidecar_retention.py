# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: image-mapping sidecars must survive a failed rewrite."""

import json
from unittest.mock import patch

from openviking.parse.image_rewrite import rewrite_image_uris
from openviking.pyagfs.exceptions import AGFSTimeoutError

ROOT = "viking://resources/doc"
MD_URI = f"{ROOT}/doc/a.md"
SIDECAR_URI = f"{ROOT}/doc/.image_mappings.json"
IMAGE_URI = f"{ROOT}/doc/logo.png"


class _RewriteFS:
    """Dict-backed FS that can fail one rewrite stage on demand."""

    def __init__(self, *, fail_read=False, fail_ls=False, fail_write=False):
        self.fail_read = fail_read
        self.fail_ls = fail_ls
        self.fail_write = fail_write
        self.files = {
            MD_URI: "![logo](./assets/logo.png)",
            SIDECAR_URI: json.dumps({"a.md": {"./assets/logo.png": "logo.png"}}),
            IMAGE_URI: "img",
        }
        self.removed = []

    async def glob(self, pattern, uri, ctx=None):
        return {"matches": [u for u in self.files if u.endswith(".md")]}

    async def read_file(self, uri, ctx=None):
        if self.fail_read and uri.endswith(".md"):
            raise AGFSTimeoutError("storage hiccup")
        return self.files[uri]

    async def ls(self, uri, ctx=None):
        if self.fail_ls:
            raise AGFSTimeoutError("storage hiccup")
        return [
            {"name": name.rsplit("/", 1)[-1], "isDir": False}
            for name in self.files
            if name.startswith(uri + "/")
        ]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        if self.fail_write:
            raise AGFSTimeoutError("storage hiccup")
        self.files[uri] = content

    async def rm(self, uri, ctx=None, lease_ref=None):
        self.removed.append(uri)
        self.files.pop(uri, None)


async def _run(fs):
    with patch("openviking.parse.image_rewrite.get_viking_fs", return_value=fs):
        return await rewrite_image_uris(ROOT, lease_ref=None)


async def test_failed_write_keeps_sidecar_for_a_later_retry():
    fs = _RewriteFS(fail_write=True)

    stats = await _run(fs)

    assert stats == {"files_processed": 0, "references_rewritten": 0}
    # The markdown still references ./assets/logo.png; the sidecar is the only
    # mapping to the stored image name.
    assert SIDECAR_URI in fs.files
    assert SIDECAR_URI not in fs.removed


async def test_failed_read_keeps_sidecar():
    fs = _RewriteFS(fail_read=True)

    await _run(fs)

    assert SIDECAR_URI in fs.files


async def test_failed_image_listing_keeps_sidecar():
    fs = _RewriteFS(fail_ls=True)

    await _run(fs)

    assert SIDECAR_URI in fs.files


async def test_successful_rewrite_still_removes_sidecar_and_rewrites_markdown():
    fs = _RewriteFS()

    stats = await _run(fs)

    assert stats == {"files_processed": 1, "references_rewritten": 1}
    assert fs.files[MD_URI] == f"![logo]({IMAGE_URI})"
    assert SIDECAR_URI not in fs.files
