# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from pathlib import Path
from types import SimpleNamespace

from openviking.prompts.manager import PromptManager
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking_cli.utils.config import (
    OPENVIKING_CONFIG_ENV,
    OPENVIKING_PROMPT_TEMPLATES_DIR_ENV,
)
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


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
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    monkeypatch.setenv(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, str(env_dir))

    manager = PromptManager(enable_caching=False)

    assert manager.templates_dir == env_dir
    assert manager.render("memory.profile") == "env-template"


def test_prompt_manager_uses_ov_conf_templates_dir_when_env_is_unset(tmp_path, monkeypatch):
    config_dir = tmp_path / "config-prompts"
    config_path = tmp_path / "ov.conf"

    _write_template(config_dir, "config-template")
    _write_config(config_path, config_dir)

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    monkeypatch.delenv(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, raising=False)

    manager = PromptManager(enable_caching=False)

    assert manager.templates_dir == config_dir
    assert manager.render("memory.profile") == "config-template"


def test_prompt_manager_falls_back_to_bundled_templates_dir(monkeypatch):
    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.delenv(OPENVIKING_CONFIG_ENV, raising=False)
    monkeypatch.delenv(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, raising=False)

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
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    monkeypatch.delenv(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, raising=False)

    manager = PromptManager(enable_caching=False)

    assert manager.render("memory.profile") == "custom-profile-template"
    bundled_template = manager.load_template("vision.image_understanding")
    assert bundled_template.metadata.id == "vision.image_understanding"


def test_memory_type_registry_loads_schemas_from_prompt_manager_resolved_templates_root(
    tmp_path, monkeypatch
):
    resolved_templates_dir = tmp_path / "resolved-prompts"
    memory_dir = resolved_templates_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "custom.yaml").write_text(
        json.dumps(
            {
                "memory_type": "custom_memory",
                "description": "custom schema from resolved prompt root",
                "directory": "viking://user/{{ user_space }}/memories/custom",
                "filename_template": "custom.md",
                "fields": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        PromptManager,
        "_resolve_templates_dir",
        classmethod(lambda cls, templates_dir=None: resolved_templates_dir),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(memory=SimpleNamespace(custom_templates_dir="")),
    )

    registry = MemoryTypeRegistry(load_schemas=True)

    assert registry.get("custom_memory") is not None


def test_memory_type_registry_prefers_custom_memory_dir_over_prompt_manager_templates_root(
    tmp_path, monkeypatch
):
    resolved_templates_dir = tmp_path / "resolved-prompts"
    resolved_memory_dir = resolved_templates_dir / "memory"
    custom_memory_dir = tmp_path / "custom-memory"
    resolved_memory_dir.mkdir(parents=True)
    custom_memory_dir.mkdir(parents=True)
    (resolved_memory_dir / "prompt_root.yaml").write_text(
        json.dumps(
            {
                "memory_type": "prompt_root_memory",
                "description": "schema from prompt manager root",
                "directory": "viking://user/{{ user_space }}/memories/prompt-root",
                "filename_template": "prompt-root.md",
                "fields": [],
            }
        ),
        encoding="utf-8",
    )
    (custom_memory_dir / "custom.yaml").write_text(
        json.dumps(
            {
                "memory_type": "custom_memory",
                "description": "schema from custom memory dir",
                "directory": "viking://user/{{ user_space }}/memories/custom",
                "filename_template": "custom.md",
                "fields": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        PromptManager,
        "_resolve_templates_dir",
        classmethod(lambda cls, templates_dir=None: resolved_templates_dir),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(custom_templates_dir=str(custom_memory_dir))
        ),
    )

    registry = MemoryTypeRegistry(load_schemas=True)

    assert registry.get("custom_memory") is not None
    assert registry.get("prompt_root_memory") is None


def test_context_provider_schema_directories_use_prompt_manager_resolved_templates_root(
    tmp_path, monkeypatch
):
    resolved_templates_dir = tmp_path / "resolved-prompts"
    expected_memory_dir = resolved_templates_dir / "memory"

    monkeypatch.setattr(
        PromptManager,
        "_resolve_templates_dir",
        classmethod(lambda cls, templates_dir=None: resolved_templates_dir),
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(custom_templates_dir="", eager_prefetch=False)
        ),
    )

    provider = SessionExtractContextProvider(messages=[])

    assert provider.get_schema_directories() == [str(expected_memory_dir)]


def test_context_provider_schema_directories_prefer_custom_memory_dir_over_prompt_manager_root(
    tmp_path, monkeypatch
):
    resolved_templates_dir = tmp_path / "resolved-prompts"
    custom_memory_dir = tmp_path / "custom-memory"

    monkeypatch.setattr(
        PromptManager,
        "_resolve_templates_dir",
        classmethod(lambda cls, templates_dir=None: resolved_templates_dir),
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(
                custom_templates_dir=str(custom_memory_dir),
                eager_prefetch=False,
            )
        ),
    )
    monkeypatch.setattr(
        "os.path.exists",
        lambda path: path == str(custom_memory_dir)
        or path == str(PromptManager._get_bundled_templates_dir() / "memory"),
    )

    provider = SessionExtractContextProvider(messages=[])

    assert provider.get_schema_directories() == [
        str(PromptManager._get_bundled_templates_dir() / "memory"),
        str(custom_memory_dir),
    ]
