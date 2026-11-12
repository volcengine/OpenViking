# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

from vikingbot.heartbeat.service import _read_heartbeat_file


def test_read_heartbeat_file_uses_utf8_with_cp936_default(tmp_path, monkeypatch):
    expected = "# Tasks\n\n- [ ] Review memory — 复查记忆\n"
    (tmp_path / "HEARTBEAT.md").write_text(expected, encoding="utf-8")
    original_read_text = Path.read_text

    def cp936_default_read_text(self, encoding=None, errors=None):
        return original_read_text(
            self,
            encoding=encoding or "cp936",
            errors=errors,
        )

    monkeypatch.setattr(Path, "read_text", cp936_default_read_text)

    assert _read_heartbeat_file(tmp_path) == expected
