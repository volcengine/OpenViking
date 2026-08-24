# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for root-suite configuration and filesystem isolation."""

import json
import os
import pytest
from pathlib import Path

from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfigSingleton,
    get_openviking_config,
    initialize_openviking_config,
)


def _write_host_style_config(path: Path, workspace: str = "/app/.openviking/data") -> None:
    """Write a sanitized host-shaped config that must never create ``/app``."""
    path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": workspace,
                    "agfs": {"backend": "local"},
                    "vectordb": {"name": "host", "backend": "local", "project": "default"},
                },
                "embedding": {
                    "dense": {
                        "provider": "litellm",
                        "model": "ollama/nomic-embed-text",
                        "api_base": "http://192.0.2.10:11434",
                        "dimension": 768,
                    }
                },
                "vlm": {
                    "provider": "litellm",
                    "model": "openai/example-model",
                    "api_base": "https://example.invalid/v1",
                },
            }
        ),
        encoding="utf-8",
    )


def test_embedded_path_is_applied_before_host_storage_validation(tmp_path, monkeypatch):
    """An embedded client path must prevent host ``/app`` creation during config parsing."""
    host_workspace = tmp_path / "host-workspace"
    host_config = tmp_path / "host-ov.conf"
    _write_host_style_config(host_config, workspace=str(host_workspace))
    workspace = tmp_path / "isolated-workspace"

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(host_config))
    try:
        config = initialize_openviking_config(path=str(workspace))
    finally:
        OpenVikingConfigSingleton.reset_instance()

    assert Path(config.storage.workspace) == workspace.resolve()
    assert Path(config.storage.agfs.path) == workspace.resolve()
    assert Path(config.storage.vectordb.path) == workspace.resolve()
    assert not host_workspace.exists()


def test_embedded_path_does_not_touch_container_workspace(tmp_path, monkeypatch):
    """A late override must not try to create the container-only host path."""
    host_config = tmp_path / "host-ov.conf"
    _write_host_style_config(host_config)
    workspace = tmp_path / "isolated-workspace"

    original_mkdir = Path.mkdir

    def reject_container_path(self, *args, **kwargs):
        if self == Path("/app") or Path("/app") in self.parents:
            raise AssertionError("embedded config attempted to create /app")
        return original_mkdir(self, *args, **kwargs)

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(host_config))
    monkeypatch.setattr(Path, "mkdir", reject_container_path)
    try:
        config = initialize_openviking_config(path=str(workspace))
    finally:
        OpenVikingConfigSingleton.reset_instance()

    assert Path(config.storage.workspace) == workspace.resolve()


def test_root_fixture_uses_function_scoped_offline_config(root_openviking_config, test_data_dir):
    """The root fixture must not inherit host providers, paths, or endpoints."""
    config = get_openviking_config()

    assert Path(config.storage.workspace).is_relative_to(test_data_dir.resolve())
    assert config.storage.workspace != "/app/.openviking/data"
    assert config.embedding.dense.provider == "litellm"
    assert config.embedding.dense.api_base is None
    assert config.vlm.provider is None
    assert config.vlm.api_base is None
    assert root_openviking_config["storage"]["workspace"] == config.storage.workspace
    assert Path(os.environ[OPENVIKING_CONFIG_ENV]).parent == test_data_dir.parent
    assert Path(os.environ[OPENVIKING_CONFIG_ENV]).name == "ov.conf"


def test_root_fixture_safe_file_contains_no_provider_endpoints(root_openviking_config):
    """The file consumed by native AGFS must be as offline as the singleton."""
    raw = json.loads(Path(os.environ[OPENVIKING_CONFIG_ENV]).read_text(encoding="utf-8"))
    assert raw["embedding"]["dense"].get("api_base") is None
    assert raw["embedding"]["dense"].get("api_key") is None
    assert raw["embedding"]["dense"].get("credentials", []) == []
    assert raw["vlm"].get("api_base") is None
    assert raw["vlm"].get("api_key") is None
    assert raw["vlm"].get("credentials", []) == []
    assert raw["vlm"].get("providers", {}) == {}


def test_root_fixture_config_file_is_private(root_openviking_config):
    """The native-readable disposable config must not be world-readable."""
    config_path = Path(os.environ[OPENVIKING_CONFIG_ENV])
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_direct_constructor_path_isolated_from_host_config(tmp_path, monkeypatch):
    """Direct service/client constructors honor their temporary path early."""
    host_config = tmp_path / "host-ov.conf"
    _write_host_style_config(host_config)
    workspace = tmp_path / "direct-constructor-workspace"

    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(host_config))
    original_mkdir = Path.mkdir

    def reject_container_path(self, *args, **kwargs):
        if self == Path("/app") or Path("/app") in self.parents:
            raise AssertionError("direct constructor attempted to create /app")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", reject_container_path)
    try:
        config = initialize_openviking_config(path=str(workspace))
    finally:
        OpenVikingConfigSingleton.reset_instance()

    assert Path(config.storage.workspace) == workspace.resolve()


def test_root_fixture_uses_deterministic_fake_embedder(root_openviking_config):
    """Embedding calls in clients using the root fixture never reach LiteLLM."""
    config = get_openviking_config()
    assert config.embedding.dimension % 4 == 0
    embedder = config.embedding.get_embedder()
    result = embedder.embed("offline regression")
    assert len(result.dense_vector) == config.embedding.dimension
    assert result.dense_vector == [0.0] * config.embedding.dimension


@pytest.mark.asyncio
async def test_root_fixture_uses_deterministic_fake_vlm(root_openviking_config):
    """VLM calls in clients using the root fixture are local fakes."""
    result = await get_openviking_config().vlm.get_completion_async("offline regression")
    assert result == "# Root fixture summary"


def test_workspace_override_rejects_non_object_config(tmp_path, monkeypatch):
    """Malformed host JSON fails before any workspace side effect."""
    config_path = tmp_path / "invalid.conf"
    config_path.write_text("[]", encoding="utf-8")
    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    try:
        with pytest.raises(ValueError, match="configuration must be a JSON object"):
            initialize_openviking_config(path=str(tmp_path / "workspace"))
    finally:
        OpenVikingConfigSingleton.reset_instance()


def test_workspace_override_rejects_non_object_storage(tmp_path, monkeypatch):
    """Malformed storage JSON fails closed instead of falling back to host paths."""
    config_path = tmp_path / "invalid-storage.conf"
    config_path.write_text(json.dumps({"storage": []}), encoding="utf-8")
    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    try:
        with pytest.raises(ValueError, match="'storage' must be an object"):
            initialize_openviking_config(path=str(tmp_path / "workspace"))
    finally:
        OpenVikingConfigSingleton.reset_instance()


def test_empty_embedded_path_preserves_legacy_no_override(tmp_path, monkeypatch):
    """An empty path retains the existing no-override behavior."""
    host_workspace = tmp_path / "host-workspace"
    config_path = tmp_path / "host.conf"
    _write_host_style_config(config_path, workspace=str(host_workspace))
    OpenVikingConfigSingleton.reset_instance()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    try:
        config = initialize_openviking_config(path="")
    finally:
        OpenVikingConfigSingleton.reset_instance()
    assert Path(config.storage.workspace) == host_workspace.resolve()


def test_cached_config_is_still_resynchronized_for_embedded_path(tmp_path):
    """A preinitialized safe singleton keeps the legacy path resynchronization contract."""
    initial = tmp_path / "initial"
    target = tmp_path / "target"
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {
                "workspace": str(initial),
                "agfs": {"backend": "local"},
                "vectordb": {"name": "test", "backend": "local", "project": "default"},
            },
            "embedding": {"dense": {"provider": "litellm", "model": "test", "dimension": 4}},
        }
    )
    try:
        config = initialize_openviking_config(path=str(target))
    finally:
        OpenVikingConfigSingleton.reset_instance()
    assert Path(config.storage.workspace) == target.resolve()
    assert Path(config.storage.agfs.path) == target.resolve()
    assert Path(config.storage.vectordb.path) == target.resolve()


def test_root_temp_dir_is_not_shared_project_state(temp_dir, tmp_path):
    """Parallel root tests must receive pytest-owned, per-test storage."""
    assert temp_dir.parent == tmp_path
    assert "test_data" not in temp_dir.parts
