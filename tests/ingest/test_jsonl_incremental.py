# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Incremental byte-offset cursor behavior for append-only JSONL sources."""

import json
import os

from openviking.ingest.sources.hermes import HermesSource
from openviking_cli.utils.config.ingest_config import IngestHarnessConfig


def _src(root):
    return HermesSource(IngestHarnessConfig(enabled=True, paths=[str(root)]), fallback_user="t")


def _rec(role, content):
    return json.dumps({"role": role, "content": content})


def test_partial_trailing_line_not_consumed(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    f = root / "s.jsonl"
    # two complete lines + a partial (no trailing newline) line
    f.write_text(_rec("user", "a") + "\n" + _rec("assistant", "b") + "\n" + _rec("user", "cc"))

    src = _src(root)
    ref = next(iter(src.discover_sessions()))
    msgs, cursor = src.read_messages(ref, None)
    assert [m.text for m in msgs] == ["a", "b"]  # partial line withheld

    # complete the partial line and append another complete line
    with open(f, "a") as fh:
        fh.write("\n" + _rec("assistant", "d") + "\n")

    msgs2, cursor2 = src.read_messages(ref, cursor)
    assert [m.text for m in msgs2] == ["cc", "d"]

    # nothing new on a third read
    msgs3, _ = src.read_messages(ref, cursor2)
    assert msgs3 == []


def test_rotation_resets_cursor(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    f = root / "s.jsonl"
    f.write_text(_rec("user", "old1") + "\n" + _rec("assistant", "old2") + "\n")

    src = _src(root)
    ref = next(iter(src.discover_sessions()))
    msgs, cursor = src.read_messages(ref, None)
    assert [m.text for m in msgs] == ["old1", "old2"]
    assert "inode" in cursor.value

    # Replace the file with brand-new, shorter content (new inode).
    os.remove(f)
    f.write_text(_rec("user", "new1") + "\n")

    msgs2, _ = src.read_messages(ref, cursor)
    assert [m.text for m in msgs2] == ["new1"]  # rotation detected -> re-read from 0
