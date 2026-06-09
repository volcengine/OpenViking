# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ``openviking-server doctor`` configuration checks."""

import json

from openviking_cli.doctor import check_config
from openviking_cli.utils.config.consts import OPENVIKING_CONFIG_ENV

VALID_CONFIG = {
    "embedding": {
        "dense": {
            "provider": "openai",
            "api_key": "test-key",
            "model": "text-embedding-3-small",
        }
    }
}


def _write_config(tmp_path, data):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path


def test_check_config_passes_for_valid_config(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path, VALID_CONFIG)
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))

    ok, detail, remediation = check_config()

    assert ok is True
    assert detail == str(config_path)
    assert remediation is None


def test_check_config_reports_unknown_nested_field(monkeypatch, tmp_path):
    # Mirrors issue #2373: ``openviking-server`` rejects these fields on
    # startup ("Unknown config field 'retrieval.top_k'"), but ``doctor``
    # previously reported "Config: PASS".
    config = dict(VALID_CONFIG, retrieval={"top_k": 10, "threshold": 0.5})
    config_path = _write_config(tmp_path, config)
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))

    ok, detail, remediation = check_config()

    assert ok is False
    assert "Unknown config field 'retrieval.top_k'" in detail
    assert remediation


def test_check_config_reports_unknown_top_level_section(monkeypatch, tmp_path):
    config = dict(VALID_CONFIG, not_a_real_section={"x": 1})
    config_path = _write_config(tmp_path, config)
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))

    ok, detail, remediation = check_config()

    assert ok is False
    assert "Unknown config field 'not_a_real_section'" in detail


def test_check_config_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(tmp_path / "does-not-exist.conf"))

    ok, detail, remediation = check_config()

    assert ok is False
    assert "not found" in detail.lower()
