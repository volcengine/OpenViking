# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the Windows UTF-8 stdio guard in ``openviking-server``.

Regression coverage for the ``init`` wizard crash on Chinese Windows (code
page 936/gbk) with redirected output — see the sibling vikingbot fix (#6929).
"""

import io
import sys

from openviking_cli import server_bootstrap


def _gbk_stream() -> io.TextIOWrapper:
    """A stdout stand-in that behaves like redirected stdio on cp936."""
    return io.TextIOWrapper(io.BytesIO(), encoding="gbk", write_through=True)


class TestForceUtf8Stdio:
    def test_win32_reconfigures_gbk_stdio_to_utf8(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        out, err = _gbk_stream(), _gbk_stream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)

        server_bootstrap._force_utf8_stdio()

        assert out.encoding.lower().replace("-", "") == "utf8"
        assert err.encoding.lower().replace("-", "") == "utf8"
        # The wizard's "✓" (U+2713) — the character that crashes on cp936 —
        # must now be printable on the same stream.
        print("\u2713 Embedding: repro", file=out)
        out.flush()

    def test_non_windows_leaves_stdio_alone(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        out = _gbk_stream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", _gbk_stream())

        server_bootstrap._force_utf8_stdio()

        assert out.encoding.lower() == "gbk"

    def test_stream_without_reconfigure_is_tolerated(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "stdout", "not-a-stream")
        monkeypatch.setattr(sys, "stderr", io.StringIO())  # no reconfigure()

        server_bootstrap._force_utf8_stdio()  # must not raise
