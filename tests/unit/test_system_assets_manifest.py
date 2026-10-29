# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from openviking.system_assets import SYSTEM_ASSETS_DIR
from scripts.build_support.system_assets_manifest import build_system_assets_manifest


def test_packaged_system_assets_manifest_is_current():
    assert (SYSTEM_ASSETS_DIR / "manifest.json").read_bytes() == build_system_assets_manifest(
        SYSTEM_ASSETS_DIR
    )
