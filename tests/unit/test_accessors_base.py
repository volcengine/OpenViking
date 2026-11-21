# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Temporary resource cleanup."""

from pathlib import Path

from openviking.parse.accessors.base import LocalResource, SourceType


def test_local_resource_cleanup_temporary(tmp_path: Path) -> None:
    """cleanup() removes temporary resources."""
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    (test_dir / "file.txt").write_text("content")

    resource = LocalResource(
        path=test_dir,
        source_type=SourceType.HTTP,
        original_source="https://example.com/reports.zip",
    )

    assert test_dir.exists()
    resource.cleanup()
    assert not test_dir.exists()
    resource.cleanup()
