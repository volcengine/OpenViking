# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from pathlib import Path

from openviking.prompts.manager import PromptManager
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

PROMPT_TEMPLATES_ENV = "OPENVIKING_PROMPT_TEMPLATES_DIR"


def _write_template(templates_dir: Path, content: str) -> None:
    template_path = templates_dir / "memory" / "profile.yaml"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "memory.profile",
                    "name": "Profile",
                    "description": "Test template",
                    "version": "1.0.0",
                    "language": "en",
                    "category": "memory",
                },
                "template": content,
            }
        ),
        encoding="utf-8",
    )


def _write_config(config_path: Path, templates_dir: Path) -> None:
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(config_path.parent / "workspace"),
                    "agfs": {"backend": "local", "mode": "binding-client"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "api_key": "test-key",
                    }
                },
                "prompts": {
                    "templates_dir": str(templates_dir),
                },
            }
        ),
        encoding="utf-8",
    )


def teardown_function() -> None:
    OpenVikingConfigSingleton.reset_instance()


def test_prompt_manager_prefers_environment_templates_dir(tmp_path, monkeypatch):
    env_dir = tmp_path / "env-prompts"
    config_dir = tmp_path / "config-prompts"
    config_path = tmp_path / "ov.conf"

    _write_template(env_dir, "env-template")
    _write_template(config_dir, "config-template")
    _write_config(config_path, config_dir)

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    monkeypatch.setenv(PROMPT_TEMPLATES_ENV, str(env_dir))

    manager = PromptManager(enable_caching=False)

    assert manager.templates_dir == env_dir
    assert manager.render("memory.profile") == "env-template"


def test_prompt_manager_uses_ov_conf_templates_dir_when_env_is_unset(tmp_path, monkeypatch):
    config_dir = tmp_path / "config-prompts"
    config_path = tmp_path / "ov.conf"

    _write_template(config_dir, "config-template")
    _write_config(config_path, config_dir)

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    monkeypatch.delenv(PROMPT_TEMPLATES_ENV, raising=False)

    manager = PromptManager(enable_caching=False)

    assert manager.templates_dir == config_dir
    assert manager.render("memory.profile") == "config-template"


def test_prompt_manager_falls_back_to_bundled_templates_dir(monkeypatch):
    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.delenv("OPENVIKING_CONFIG_FILE", raising=False)
    monkeypatch.delenv(PROMPT_TEMPLATES_ENV, raising=False)

    manager = PromptManager(enable_caching=False)

    assert manager.templates_dir == PromptManager._get_bundled_templates_dir()


def test_prompt_manager_falls_back_to_bundled_template_when_custom_dir_is_partial(
    tmp_path, monkeypatch
):
    custom_dir = tmp_path / "custom-prompts"
    config_path = tmp_path / "ov.conf"

    _write_template(custom_dir, "custom-profile-template")
    _write_config(config_path, custom_dir)

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    monkeypatch.delenv(PROMPT_TEMPLATES_ENV, raising=False)

    manager = PromptManager(enable_caching=False)

    assert manager.render("memory.profile") == "custom-profile-template"
    bundled_template = manager.load_template("vision.image_understanding")
    assert bundled_template.metadata.id == "vision.image_understanding"
