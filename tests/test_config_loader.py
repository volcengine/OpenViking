# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for config_loader utilities."""

import pytest

from openviking_cli.utils.config.config_loader import (
    load_json_config,
    require_config,
    resolve_config_path,
)


class TestResolveConfigPath:
    """Tests for resolve_config_path."""

    def test_explicit_path_exists(self, tmp_path):
        conf = tmp_path / "test.conf"
        conf.write_text("{}")
        result = resolve_config_path(str(conf), "UNUSED_ENV", "unused.conf")
        assert result == conf

    def test_explicit_path_not_exists(self, tmp_path):
        result = resolve_config_path(
            str(tmp_path / "nonexistent.conf"), "UNUSED_ENV", "unused.conf"
        )
        assert result is None

    def test_env_var_path(self, tmp_path, monkeypatch):
        conf = tmp_path / "env.conf"
        conf.write_text("{}")
        monkeypatch.setenv("TEST_CONFIG_ENV", str(conf))
        result = resolve_config_path(None, "TEST_CONFIG_ENV", "unused.conf")
        assert result == conf

    def test_env_var_path_not_exists(self, monkeypatch):
        monkeypatch.setenv("TEST_CONFIG_ENV", "/nonexistent/path.conf")
        result = resolve_config_path(None, "TEST_CONFIG_ENV", "unused.conf")
        assert result is None

    def test_default_path(self, tmp_path, monkeypatch):
        import openviking_cli.utils.config.config_loader as loader

        conf = tmp_path / "ov.conf"
        conf.write_text("{}")
        monkeypatch.setattr(loader, "DEFAULT_CONFIG_DIR", tmp_path)
        monkeypatch.delenv("TEST_CONFIG_ENV", raising=False)
        result = resolve_config_path(None, "TEST_CONFIG_ENV", "ov.conf")
        assert result == conf

    def test_nothing_found(self, monkeypatch):
        monkeypatch.delenv("TEST_CONFIG_ENV", raising=False)
        result = resolve_config_path(None, "TEST_CONFIG_ENV", "nonexistent.conf")
        # May or may not be None depending on whether ~/.openviking/nonexistent.conf exists
        # but for a random filename it should be None
        assert result is None

    def test_explicit_takes_priority_over_env(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit.conf"
        explicit.write_text('{"source": "explicit"}')
        env_conf = tmp_path / "env.conf"
        env_conf.write_text('{"source": "env"}')
        monkeypatch.setenv("TEST_CONFIG_ENV", str(env_conf))
        result = resolve_config_path(str(explicit), "TEST_CONFIG_ENV", "unused.conf")
        assert result == explicit


class TestLoadJsonConfig:
    """Tests for load_json_config."""

    def test_valid_json(self, tmp_path):
        conf = tmp_path / "test.conf"
        conf.write_text('{"key": "value", "num": 42}')
        data = load_json_config(conf)
        assert data == {"key": "value", "num": 42}

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_json_config(tmp_path / "nonexistent.conf")

    def test_invalid_json(self, tmp_path):
        conf = tmp_path / "bad.conf"
        conf.write_text("not valid json {{{")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_json_config(conf)


class TestRequireConfig:
    """Tests for require_config."""

    def test_loads_existing_config(self, tmp_path):
        conf = tmp_path / "test.conf"
        conf.write_text('{"url": "http://localhost:1933"}')
        data = require_config(str(conf), "UNUSED_ENV", "unused.conf", "test")
        assert data["url"] == "http://localhost:1933"

    def test_raises_on_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_MISSING_ENV", raising=False)
        with pytest.raises(FileNotFoundError, match="configuration file not found"):
            require_config(None, "TEST_MISSING_ENV", "nonexistent_file.conf", "test")
