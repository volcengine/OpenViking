# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for config_loader utilities."""

import pytest

from openviking_cli.utils.config import (
    OPENVIKING_CONFIG_ENV,
)
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

    def test_expands_environment_variables(self, tmp_path, monkeypatch):
        conf = tmp_path / "env.conf"
        conf.write_text('{"api_key": "${TEST_API_KEY}"}')
        monkeypatch.setenv("TEST_API_KEY", "sk-test-123")

        data = load_json_config(conf)

        assert data == {"api_key": "sk-test-123"}


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


def test_openviking_config_rejects_unknown_nested_parser_section(monkeypatch):
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, "/tmp/codex-no-config.json")

    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    with pytest.raises(ValueError, match="markdown"):
        OpenVikingConfig.from_dict(
            {
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "api_key": "test-key",
                        "model": "text-embedding-3-small",
                    }
                },
                "parsers": {"markdwon": {}},
            }
        )

    OpenVikingConfigSingleton.reset_instance()


def test_openviking_config_rejects_unknown_top_level_section_with_suggestion(monkeypatch):
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, "/tmp/codex-no-config.json")

    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    with pytest.raises(
        ValueError, match=r"Unknown config field 'erver' in OpenVikingConfig .*'server'"
    ):
        OpenVikingConfig.from_dict(
            {
                "erver": {
                    "host": "127.0.0.1",
                    "port": 1933,
                    "root_api_key": "test",
                    "cors_origins": ["*"],
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "api_key": "test-key",
                        "model": "text-embedding-3-small",
                    }
                },
            }
        )

    OpenVikingConfigSingleton.reset_instance()


def test_openviking_config_warns_when_agent_scope_mode_is_configured(monkeypatch, caplog):
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", "/tmp/codex-no-config.json")

    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    with caplog.at_level("WARNING"):
        config = OpenVikingConfig.from_dict({"memory": {"agent_scope_mode": "agent"}})

    assert config.memory.agent_scope_mode == "agent"
    assert "memory.agent_scope_mode is deprecated and ignored" in caplog.text

    OpenVikingConfigSingleton.reset_instance()


def test_openviking_config_retrieval_hotness_alpha_defaults_to_zero(monkeypatch):
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, "/tmp/codex-no-config.json")

    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    config = OpenVikingConfig.from_dict({})

    assert config.retrieval.hotness_alpha == 0.0
    assert config.retrieval.score_propagation_alpha == 0.5

    OpenVikingConfigSingleton.reset_instance()


@pytest.mark.parametrize("field_name", ["hotness_alpha", "score_propagation_alpha"])
def test_openviking_config_retrieval_alpha_validates_range(monkeypatch, field_name):
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, "/tmp/codex-no-config.json")

    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    with pytest.raises(ValueError):
        OpenVikingConfig.from_dict({"retrieval": {field_name: 1.5}})

    OpenVikingConfigSingleton.reset_instance()


def test_openviking_config_singleton_preserves_value_error_for_bad_config(tmp_path, monkeypatch):
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, "/tmp/codex-no-config.json")

    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        '{"erver": {"host": "127.0.0.1"}, "embedding": {"dense": {"provider": "openai", "api_key": "x", "model": "m"}}}'
    )

    OpenVikingConfigSingleton.reset_instance()
    with pytest.raises(ValueError, match="server"):
        OpenVikingConfigSingleton.initialize(config_path=str(config_path))
    OpenVikingConfigSingleton.reset_instance()


def test_require_config_missing_message_uses_openviking_ai_docs(tmp_path, monkeypatch):
    import openviking_cli.utils.config.config_loader as loader

    monkeypatch.delenv("TEST_MISSING_ENV", raising=False)
    monkeypatch.setattr(loader, "DEFAULT_CONFIG_DIR", tmp_path / "user")
    monkeypatch.setattr(loader, "SYSTEM_CONFIG_DIR", tmp_path / "system")

    with pytest.raises(FileNotFoundError, match=r"https://openviking\.ai/docs"):
        loader.require_config(None, "TEST_MISSING_ENV", "missing.conf", "test")


def test_load_server_config_missing_message_uses_openviking_ai_docs(tmp_path, monkeypatch):
    import openviking.server.config as server_config

    monkeypatch.delenv(OPENVIKING_CONFIG_ENV, raising=False)
    monkeypatch.setattr(server_config, "DEFAULT_CONFIG_DIR", tmp_path / "user")
    monkeypatch.setattr(server_config, "SYSTEM_CONFIG_DIR", tmp_path / "system")

    with pytest.raises(FileNotFoundError, match=r"https://openviking\.ai/docs"):
        server_config.load_server_config()


def test_openviking_config_singleton_missing_message_uses_openviking_ai_docs(tmp_path, monkeypatch):
    import openviking_cli.utils.config.open_viking_config as config_module
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    monkeypatch.delenv(OPENVIKING_CONFIG_ENV, raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_DIR", tmp_path / "user")
    monkeypatch.setattr(config_module, "SYSTEM_CONFIG_DIR", tmp_path / "system")

    OpenVikingConfigSingleton.reset_instance()
    try:
        with pytest.raises(FileNotFoundError, match=r"https://openviking\.ai/docs"):
            OpenVikingConfigSingleton.get_instance()
    finally:
        OpenVikingConfigSingleton.reset_instance()


def test_openviking_config_singleton_initialize_missing_message_uses_openviking_ai_docs(
    tmp_path, monkeypatch
):
    import openviking_cli.utils.config.open_viking_config as config_module
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    monkeypatch.delenv(OPENVIKING_CONFIG_ENV, raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_DIR", tmp_path / "user")
    monkeypatch.setattr(config_module, "SYSTEM_CONFIG_DIR", tmp_path / "system")

    OpenVikingConfigSingleton.reset_instance()
    try:
        with pytest.raises(FileNotFoundError, match=r"https://openviking\.ai/docs"):
            OpenVikingConfigSingleton.initialize()
    finally:
        OpenVikingConfigSingleton.reset_instance()
