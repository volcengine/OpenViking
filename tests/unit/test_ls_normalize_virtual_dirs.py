#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Virtual-directory ls rows must stay listable without modTime (#4859)."""

from openviking.storage.viking_fs._ops import _OpsMixin


def test_normalize_ls_entry_fills_missing_directory_metadata():
    entry = _OpsMixin._normalize_ls_entry(
        {"name": "my-doc", "isDir": True, "mode": 0o755}
    )

    assert entry["isDir"] is True
    assert entry["size"] == 0
    assert entry.get("modTime")


def test_normalize_ls_entry_infers_directory_from_mode_bits():
    entry = _OpsMixin._normalize_ls_entry(
        {"name": "my-doc", "mode": 0o040755, "size": 0}
    )

    assert entry["isDir"] is True
    assert entry.get("modTime")
