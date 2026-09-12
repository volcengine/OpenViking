# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the file-content md5 fingerprint links used by incremental diff.

MD5 is computed once at the final-bytes upload site and threaded down through
Context; nothing reads a file back just to hash it. These tests pin the helper,
the Context field round-trip, and the empty-omission rule that keeps a partial
update from clearing an existing fingerprint.
"""

from openviking.core.context import Context
from openviking.utils.content_hash import content_md5


class TestContentMd5:
    def test_stable_hex_digest(self) -> None:
        assert content_md5(b"print(1)") == content_md5(b"print(1)")
        assert content_md5(b"a") != content_md5(b"b")
        assert len(content_md5(b"x")) == 32


class TestContextMd5Field:
    def test_roundtrip_preserves_md5(self) -> None:
        md5 = content_md5(b"print(1)")
        ctx = Context(uri="viking://resources/x/a.py", is_leaf=True, md5=md5)
        assert ctx.to_dict()["md5"] == md5
        assert Context.from_dict(ctx.to_dict()).md5 == md5

    def test_missing_md5_is_omitted_from_dict(self) -> None:
        # Omitting an empty md5 keeps a partial update from clearing an existing
        # fingerprint on the stored record.
        ctx = Context(uri="viking://resources/x/b.py", is_leaf=True)
        assert "md5" not in ctx.to_dict()
        assert Context.from_dict({"uri": "viking://resources/x/b.py"}).md5 == ""
