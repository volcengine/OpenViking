"""Tests for sandbox configuration schema."""

import pytest
from vikingbot.config.schema import (
    SandboxNetworkConfig,
    SandboxFilesystemConfig,
    SandboxRuntimeConfig,
    SrtBackendConfig,
    DockerBackendConfig,
    SandboxBackendsConfig,
    SandboxConfig,
)


def test_sandbox_network_config_defaults():
    """Test default values for SandboxNetworkConfig."""
    config = SandboxNetworkConfig()
    assert config.allowed_domains == []
    assert config.denied_domains == []
    assert config.allow_local_binding is False


def test_sandbox_filesystem_config_defaults():
    """Test default values for SandboxFilesystemConfig."""
    config = SandboxFilesystemConfig()
    assert config.deny_read == []
    assert config.allow_write == []
    assert config.deny_write == []


def test_sandbox_runtime_config_defaults():
    """Test default values for SandboxRuntimeConfig."""
    config = SandboxRuntimeConfig()
    assert config.cleanup_on_exit is True
    assert config.timeout == 300


def test_srt_backend_config_defaults():
    """Test default values for SrtBackendConfig."""
    config = SrtBackendConfig()
    assert config.settings_path == "~/.vikingbot/srt-settings.json"


def test_docker_backend_config_defaults():
    """Test default values for DockerBackendConfig."""
    config = DockerBackendConfig()
    assert config.image == "python:3.11-slim"
    assert config.network_mode == "bridge"


def test_sandbox_backends_config_defaults():
    """Test default values for SandboxBackendsConfig."""
    config = SandboxBackendsConfig()
    assert isinstance(config.srt, SrtBackendConfig)
    assert isinstance(config.docker, DockerBackendConfig)


def test_sandbox_config_defaults():
    """Test default values for SandboxConfig."""
    config = SandboxConfig()
    assert config.enabled is False
    assert config.backend == "srt"
    assert config.mode == "disabled"
    assert isinstance(config.network, SandboxNetworkConfig)
    assert isinstance(config.filesystem, SandboxFilesystemConfig)
    assert isinstance(config.runtime, SandboxRuntimeConfig)
    assert isinstance(config.backends, SandboxBackendsConfig)


def test_sandbox_config_custom_values():
    """Test SandboxConfig with custom values."""
    config = SandboxConfig(
        enabled=True,
        backend="docker",
        mode="per-session",
    )
    assert config.enabled is True
    assert config.backend == "docker"
    assert config.mode == "per-session"


def test_sandbox_network_config_custom_values():
    """Test SandboxNetworkConfig with custom values."""
    config = SandboxNetworkConfig(
        allowed_domains=["example.com"],
        denied_domains=["malicious.com"],
        allow_local_binding=True,
    )
    assert config.allowed_domains == ["example.com"]
    assert config.denied_domains == ["malicious.com"]
    assert config.allow_local_binding is True


def test_sandbox_filesystem_config_custom_values():
    """Test SandboxFilesystemConfig with custom values."""
    config = SandboxFilesystemConfig(
        deny_read=["~/.ssh"],
        allow_write=["~/.vikingbot/workspace"],
        deny_write=[".env"],
    )
    assert config.deny_read == ["~/.ssh"]
    assert config.allow_write == ["~/.vikingbot/workspace"]
    assert config.deny_write == [".env"]
