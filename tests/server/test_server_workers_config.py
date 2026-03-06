# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for server workers configuration (issue #464).

Verifies that:
- ServerConfig defaults to workers=1
- workers can be overridden via ov.conf ``server.workers``
- validate_server_config rejects workers < 1
- valid workers values (1, N > 1) pass validation
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from openviking.server.config import ServerConfig, validate_server_config


# ---------------------------------------------------------------------------
# ServerConfig defaults
# ---------------------------------------------------------------------------


def test_server_config_default_workers():
    """ServerConfig should default to a single worker."""
    config = ServerConfig()
    assert config.workers == 1


def test_server_config_custom_workers():
    """ServerConfig workers field should accept any positive integer."""
    config = ServerConfig(workers=4)
    assert config.workers == 4


# ---------------------------------------------------------------------------
# validate_server_config – workers validation
# ---------------------------------------------------------------------------


def test_validate_rejects_zero_workers():
    """workers=0 should cause sys.exit(1)."""
    config = ServerConfig(workers=0, root_api_key="test-key")
    with pytest.raises(SystemExit) as exc_info:
        validate_server_config(config)
    assert exc_info.value.code == 1


def test_validate_rejects_negative_workers():
    """workers=-1 should cause sys.exit(1)."""
    config = ServerConfig(workers=-1, root_api_key="test-key")
    with pytest.raises(SystemExit) as exc_info:
        validate_server_config(config)
    assert exc_info.value.code == 1


def test_validate_accepts_single_worker():
    """workers=1 (default) should pass validation."""
    config = ServerConfig(workers=1, root_api_key="test-key")
    # Should not raise
    validate_server_config(config)


def test_validate_accepts_multiple_workers():
    """workers=4 should pass validation."""
    config = ServerConfig(workers=4, root_api_key="test-key")
    # Should not raise
    validate_server_config(config)


# ---------------------------------------------------------------------------
# load_server_config – reads workers from config dict
# ---------------------------------------------------------------------------


def test_load_server_config_reads_workers(tmp_path):
    """load_server_config should populate workers from the server section."""
    import json

    conf = tmp_path / "ov.conf"
    conf.write_text(json.dumps({"server": {"workers": 8, "root_api_key": "test-key"}}))

    import os

    with patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(conf)}):
        from openviking.server.config import load_server_config

        config = load_server_config(str(conf))
        assert config.workers == 8


def test_load_server_config_defaults_workers_to_one(tmp_path):
    """load_server_config should default workers to 1 when not in config."""
    import json

    conf = tmp_path / "ov.conf"
    conf.write_text(json.dumps({"server": {"root_api_key": "test-key"}}))

    import os

    with patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(conf)}):
        from openviking.server.config import load_server_config

        config = load_server_config(str(conf))
        assert config.workers == 1
