# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ovcli.conf stays loadable by every reader that claims to read it."""

import json
import sys
from pathlib import Path

import pytest

from openviking_cli.utils.config.ovcli_config import load_ovcli_config as load_cli_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "ovcli.conf.example"

sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))
from openviking_sdk.config import load_ovcli_config as load_sdk_config  # noqa: E402

LOADERS = pytest.mark.parametrize("load", [load_cli_config, load_sdk_config], ids=["cli", "sdk"])


@LOADERS
def test_shipped_example_loads(load):
    assert load(str(EXAMPLE)) is not None


@LOADERS
def test_plugin_section_is_accepted(load, tmp_path):
    path = tmp_path / "ovcli.conf"
    path.write_text(
        json.dumps({"url": "http://localhost:1933", "plugin": {"recallCompress": "off"}})
    )

    assert load(str(path)) is not None


@LOADERS
def test_unknown_field_is_still_rejected(load, tmp_path):
    path = tmp_path / "ovcli.conf"
    path.write_text(json.dumps({"url": "http://localhost:1933", "nonsense": 1}))

    with pytest.raises(ValueError):
        load(str(path))
