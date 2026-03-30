#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.agfs_manager import AGFSManager
from openviking_cli.utils.config.agfs_config import AGFSConfig, DirectoryMarkerMode, S3Config


def _build_s3_config(**overrides) -> S3Config:
    return S3Config(
        bucket="my-bucket",
        region="us-west-1",
        access_key="fake-access-key-for-testing",
        secret_key="fake-secret-key-for-testing-12345",
        endpoint="https://tos-cn-beijing.volces.com",
        **overrides,
    )


def test_s3_directory_marker_mode_defaults_to_empty():
    default_s3 = S3Config()

    assert default_s3.directory_marker_mode is DirectoryMarkerMode.EMPTY


def test_s3_rejects_removed_legacy_nonempty_directory_marker_alias():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        _build_s3_config(nonempty_directory_marker=True)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (DirectoryMarkerMode.EMPTY, "empty"),
        (DirectoryMarkerMode.NONEMPTY, "nonempty"),
        (DirectoryMarkerMode.NONE, "none"),
    ],
)
def test_agfs_manager_emits_directory_marker_mode_only(tmp_path, mode, expected):
    config = AGFSConfig(
        path=str(tmp_path),
        backend="s3",
        s3=_build_s3_config(directory_marker_mode=mode),
    )

    manager = AGFSManager(config=config)
    agfs_config = manager._generate_config()
    s3_plugin_config = agfs_config["plugins"]["s3fs"]["config"]

    assert s3_plugin_config["directory_marker_mode"] == expected
