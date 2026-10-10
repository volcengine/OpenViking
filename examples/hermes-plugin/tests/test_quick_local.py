"""Quick Local contracts, ported from Hermes #94851 through the external loader."""

from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture(autouse=True)
def plugin_modules(external_provider, monkeypatch):
    global quick_local, setup_flow, openviking_module, OpenVikingMemoryProvider
    _home, provider, openviking_module, _settings = external_provider("quick-local")
    OpenVikingMemoryProvider = type(provider)
    quick_local = importlib.import_module(openviking_module.__name__ + ".quick_local")
    setup_flow = importlib.import_module(openviking_module.__name__ + "._setup")
    monkeypatch.setattr(quick_local.secrets, "token_urlsafe", lambda _size: "local-test-key")
    monkeypatch.setattr(quick_local, "_validate_local_embedding", MagicMock())
    monkeypatch.setattr(quick_local, "_validate_vlm", MagicMock())
    packages = importlib.import_module(openviking_module.__name__ + ".local_packages")
    monkeypatch.setattr(
        packages, "resolve_server_requirement", lambda **_kw: "openviking[local-embed]==0.4.23"
    )


def _preflight(tmp_path: Path) -> quick_local.QuickLocalPreflight:
    return quick_local.QuickLocalPreflight(
        paths=quick_local.managed_paths(tmp_path),
        reusable_endpoint=None,
    )


def test_build_server_config_uses_profile_scoped_storage_and_local_embedding(
    tmp_path,
):
    paths = quick_local.managed_paths(tmp_path)

    config = quick_local.build_server_config(
        paths,
        {
            "provider": "openai",
            "model": "model-1",
            "api_key": "secret",
            "api_base": "https://llm.example/v1",
        },
        port=1941,
    )

    assert config == {
        "server": {
            "host": "127.0.0.1",
            "port": 1941,
            "auth_mode": "trusted",
            "root_api_key": "local-test-key",
        },
        "storage": {"workspace": str(tmp_path / "openviking" / "data")},
        "embedding": {
            "dense": {
                "provider": "local",
                "model": "bge-small-zh-v1.5-f16",
                "dimension": 512,
                "cache_dir": str(tmp_path / "openviking" / "models"),
            }
        },
        "vlm": {
            "provider": "openai",
            "model": "model-1",
            "api_key": "secret",
            "api_base": "https://llm.example/v1",
        },
    }


def test_resolve_vlm_uses_one_persisted_hermes_model_source(monkeypatch):
    from hermes_cli import config as config_module
    from hermes_cli import runtime_provider

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: {"model": {"provider": "custom:test", "default": "saved-model"}},
    )
    resolver = MagicMock(
        return_value={
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "https://llm.example/v1",
            "api_key": "secret",
            "source": "custom_provider:test",
            "extra_headers": {"X-Tenant": "tenant"},
            "request_overrides": {"extra_body": {"thinking": {"type": "disabled"}}},
        }
    )
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", resolver)

    vlm = quick_local.resolve_hermes_vlm_config()

    resolver.assert_called_once_with(
        requested="custom:test",
        target_model="saved-model",
    )
    assert vlm == {
        "provider": "openai",
        "model": "saved-model",
        "api_key": "secret",
        "api_base": "https://llm.example/v1",
        "extra_headers": {"X-Tenant": "tenant"},
        "extra_request_body": {"thinking": {"type": "disabled"}},
        "temperature": 0.0,
        "max_retries": 2,
    }


def test_resolve_vlm_maps_anthropic_transport(monkeypatch):
    from hermes_cli import config as config_module
    from hermes_cli import runtime_provider

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: {"model": {"provider": "anthropic", "default": "claude-sonnet"}},
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "anthropic",
            "api_mode": "anthropic_messages",
            "base_url": "https://api.anthropic.com",
            "api_key": "secret",
            "source": "env",
            "model": "claude-sonnet",
        },
    )

    vlm = quick_local.resolve_hermes_vlm_config()

    assert vlm["provider"] == "litellm"
    assert vlm["model"] == "anthropic/claude-sonnet"
    assert vlm["api_base"] == "https://api.anthropic.com"


@pytest.mark.parametrize("suffix", ["/anthropic", "/anthropic/v1", "/anthropic/v1/"])
def test_resolve_vlm_normalizes_only_anthropic_version_suffix(monkeypatch, suffix):
    from hermes_cli import config, runtime_provider

    monkeypatch.setattr(config, "load_config", lambda: {
        "model": {"provider": "custom:gateway", "default": "claude-test"}
    })
    runtime = {
        "provider": "custom", "api_mode": "anthropic_messages",
        "base_url": "https://gateway.example" + suffix,
        "api_key": "static-proxy-key", "source": "custom_provider:gateway",
    }
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_: runtime)
    assert quick_local.resolve_hermes_vlm_config()["api_base"] == "https://gateway.example/anthropic"
    runtime["api_mode"] = "chat_completions"
    assert quick_local.resolve_hermes_vlm_config()["api_base"] == runtime["base_url"]


@pytest.mark.parametrize("credential", ["eyJ-test-only", "cc-test-only", "sk-ant-oat01-test", "sk-ant-setup-test"])
@pytest.mark.parametrize("declared", [False, True])
def test_real_resolver_rejects_native_anthropic_oauth_before_declared_key(
    external_provider, credential, declared
):
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home, _, _, _ = external_provider("oauth-source")
    model = {"provider": "anthropic", "default": "claude-test"}
    if declared:
        model["key_env"] = "ANTHROPIC_TOKEN"
    (home / "config.yaml").write_text(json.dumps({"model": model}))
    (home / ".env").write_text("ANTHROPIC_TOKEN=" + credential + "\n")
    home_token = set_hermes_home_override(home)
    secret_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
    try:
        with pytest.raises(quick_local.QuickLocalSetupError, match="cannot be copied safely"):
            quick_local.resolve_hermes_vlm_config()
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


@pytest.mark.parametrize("base", ["https://api.anthropic.com/v1", "https://proxy.example/anthropic/v1"])
@pytest.mark.parametrize("credential", ["eyJ-test-only", "cc-test-only", "sk-ant-api03-static-test"])
def test_anthropic_credentials_follow_route_identity_before_declared_key(monkeypatch, base, credential):
    from hermes_cli import config, runtime_provider

    monkeypatch.setattr(config, "load_config", lambda: {
        "model": {"provider": "custom:proxy", "default": "claude-test", "key_env": "PROXY_KEY"}
    })
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_: {
        "provider": "custom", "api_mode": "anthropic_messages", "base_url": base,
        "api_key": credential, "source": "env:PROXY_KEY",
    })
    if "api.anthropic.com" in base and not credential.startswith("sk-ant-api"):
        with pytest.raises(quick_local.QuickLocalSetupError, match="cannot be copied safely"):
            quick_local.resolve_hermes_vlm_config()
    else:
        assert quick_local.resolve_hermes_vlm_config()["api_key"] == credential


def test_resolve_vlm_accepts_declared_model_key_env(monkeypatch):
    from hermes_cli import auth, config, runtime_provider
    saved = {"model": {"provider": "lmstudio", "default": "local-model", "key_env": "TEST_LLM_KEY"}}
    monkeypatch.setattr(config, "load_config", lambda: saved)
    monkeypatch.setattr(config, "get_env_value_prefer_dotenv", lambda name: "static-test-key" if name == "TEST_LLM_KEY" else "")
    key, source = auth._resolve_api_key_provider_secret("lmstudio", auth.PROVIDER_REGISTRY["lmstudio"])
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_kwargs: {
        "provider": "lmstudio", "source": source, "api_key": key,
        "base_url": "http://127.0.0.1:1234/v1", "api_mode": "chat_completions"})
    assert quick_local.resolve_hermes_vlm_config()["api_key"] == "static-test-key"


@pytest.mark.parametrize("key", ["", "local-static-key"])
def test_resolve_vlm_accepts_hermes_local_runtime(monkeypatch, key):
    from hermes_cli import config, runtime_provider, runtime_provider_custom
    endpoint = pytest.importorskip("hermes_cli.local_runtime.endpoint")
    monkeypatch.setattr(config, "load_config", lambda: {"model": {"default": "local-model", "provider": "llamacpp"}})
    monkeypatch.setattr(endpoint, "resolve_llamacpp_endpoint", lambda **_kwargs: {
        "base_url": "http://127.0.0.1:8090/v1", "api_key": key})
    runtime = runtime_provider_custom._resolve_llamacpp_runtime("llamacpp", None)
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_kwargs: runtime)
    assert quick_local.resolve_hermes_vlm_config()["api_key"] == (key or "no-key-required")


def test_resolve_vlm_accepts_structured_persisted_default(monkeypatch):
    from hermes_cli import config as config_module
    from hermes_cli import runtime_provider

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: {"model": {"default": {"provider": "custom:corp", "model": "corp-model"}}},
    )
    resolver = MagicMock(
        return_value={
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "https://llm.example/v1",
            "api_key": "secret",
            "source": "custom_provider:corp",
        }
    )
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", resolver)

    vlm = quick_local.resolve_hermes_vlm_config()

    resolver.assert_called_once_with(
        requested="custom:corp",
        target_model="corp-model",
    )
    assert vlm["model"] == "corp-model"


@pytest.mark.parametrize(
    ("runtime", "message"),
    [
        (
            {
                "provider": "openai-codex",
                "api_mode": "codex_responses",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_key": "short-lived",
                "source": "oauth",
            },
            "cannot be copied safely",
        ),
        (
            {
                "provider": "anthropic",
                "api_mode": "anthropic_messages",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-ant-oat01-short-lived",
                "source": "env",
            },
            "cannot be copied safely",
        ),
        (
            {
                "provider": "custom",
                "api_mode": "chat_completions",
                "base_url": "https://llm.example/v1",
                "api_key": lambda: pytest.fail("Setup must not execute key commands"),
                "source": "key_cmd",
            },
            "cannot be copied safely",
        ),
        (
            {
                "provider": "copilot",
                "api_mode": "chat_completions",
                "base_url": "https://api.githubcopilot.com",
                "api_key": "short-lived-exchanged-token",
                "source": "GH_TOKEN",
            },
            "cannot be copied safely",
        ),
        (
            {
                "provider": "future-oauth-provider",
                "api_mode": "chat_completions",
                "base_url": "https://llm.example/v1",
                "api_key": "short-lived",
                "source": "future-credential-store",
            },
            "cannot be copied safely",
        ),
        (
            {
                "provider": "openai-api",
                "api_mode": "codex_responses",
                "base_url": "https://responses-only.example/v1",
                "api_key": "secret",
                "source": "OPENAI_API_KEY",
            },
            "transport is not supported",
        ),
        (
            {
                "provider": "custom",
                "api_mode": "chat_completions",
                "base_url": "https://llm.example/v1",
                "api_key": False,
                "source": "env/config",
            },
            "cannot be copied safely",
        ),
        (
            {
                "provider": "custom",
                "api_mode": "chat_completions",
                "base_url": {"url": "https://llm.example/v1"},
                "api_key": "secret",
                "source": "env/config",
            },
            "API base URL must be a string",
        ),
        (
            {
                "provider": "custom",
                "api_mode": "chat_completions",
                "base_url": False,
                "api_key": "secret",
                "source": "env/config",
            },
            "API base URL must be a string",
        ),
    ],
)
def test_resolve_vlm_rejects_credentials_or_transports_openviking_cannot_reuse(
    runtime,
    message,
    monkeypatch,
):
    from hermes_cli import config as config_module
    from hermes_cli import runtime_provider

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: {"model": {"provider": "openai", "default": "model-1"}},
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: runtime,
    )

    with pytest.raises(quick_local.QuickLocalSetupError, match=message):
        quick_local.resolve_hermes_vlm_config()


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("api_key", "did not resolve reusable static credentials"),
        ("base_url", "did not resolve an API base URL"),
    ],
)
@pytest.mark.parametrize("missing_value", [None, "", " \t "])
def test_resolve_vlm_reports_missing_fields_before_classifying_credentials(
    field, message, missing_value, monkeypatch
):
    from hermes_cli import config as config_module
    from hermes_cli import runtime_provider

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: {"model": {"provider": "custom", "default": "model-1"}},
    )
    runtime = {
        "provider": "custom",
        "api_mode": "chat_completions",
        "base_url": "https://llm.example/v1",
        "api_key": "secret",
        "source": "env/config",
        field: missing_value,
    }
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_kwargs: runtime)
    classify = MagicMock(wraps=quick_local._has_copyable_static_credentials)
    monkeypatch.setattr(quick_local, "_has_copyable_static_credentials", classify)

    with pytest.raises(quick_local.QuickLocalSetupError, match=message):
        quick_local.resolve_hermes_vlm_config()

    classify.assert_not_called()


def test_existing_openviking_without_local_embedding_runtime_is_not_reused(
    tmp_path,
    monkeypatch,
):
    paths = quick_local.managed_paths(tmp_path)
    paths.runtime_python.parent.mkdir(parents=True)
    paths.runtime_python.touch()
    paths.server_command.touch()
    run = MagicMock(
        return_value=SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'llama_cpp'",
        )
    )
    monkeypatch.setattr(quick_local.subprocess, "run", run)

    assert quick_local.openviking_install_satisfies_requirement(paths) is False
    assert "import llama_cpp" in run.call_args.args[0][2]


def test_existing_openviking_with_local_embedding_runtime_is_reused(
    tmp_path,
    monkeypatch,
):
    paths = quick_local.managed_paths(tmp_path)
    paths.runtime_python.parent.mkdir(parents=True)
    paths.runtime_python.touch()
    paths.server_command.touch()
    monkeypatch.setattr(
        quick_local.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="0.4.22\n",
            stderr="",
        ),
    )

    assert quick_local.openviking_install_satisfies_requirement(paths) is True


def test_validation_server_does_not_inherit_stdin(tmp_path, monkeypatch):
    server_command = tmp_path / "openviking-server"
    server_command.write_text("", encoding="utf-8")
    config_path = tmp_path / "ov.conf"
    config_path.write_text("{}", encoding="utf-8")
    process = MagicMock()
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(quick_local.subprocess, "Popen", popen)
    monkeypatch.setattr(quick_local, "_can_bind_local_port", lambda *_args: True)

    result = quick_local._start_validation_server(
        "http://127.0.0.1:1933",
        config_path,
        tmp_path,
        server_command,
    )

    assert result is process
    assert popen.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_validation_server_rechecks_selected_port_before_start(tmp_path, monkeypatch):
    server_command = tmp_path / "openviking-server"
    server_command.write_text("", encoding="utf-8")
    config_path = tmp_path / "ov.conf"
    config_path.write_text("{}", encoding="utf-8")
    popen = MagicMock()
    monkeypatch.setattr(quick_local.subprocess, "Popen", popen)
    monkeypatch.setattr(quick_local, "_can_bind_local_port", lambda *_args: False)

    with pytest.raises(quick_local.QuickLocalSetupError, match="became unavailable"):
        quick_local._start_validation_server(
            "http://127.0.0.1:1933",
            config_path,
            tmp_path,
            server_command,
        )

    popen.assert_not_called()


def test_reuse_rechecks_runtime_and_refreshes_saved_vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(quick_local, "server_belongs_to_profile", lambda *_args: True)
    lifecycle = importlib.import_module(quick_local.__package__ + ".local_server")
    monkeypatch.setattr(lifecycle.LocalServer, "_verified_process", lambda *_args: object())
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir(parents=True)
    old_vlm = {
        "provider": "openai",
        "model": "old-model",
        "api_key": "old-secret",
        "api_base": "https://old.example/v1",
    }
    quick_local.atomic_json_write(
        paths.server_config,
        quick_local.build_server_config(paths, old_vlm, port=1938),
        mode=0o600,
    )
    quick_local._write_ovcli_profile(
        paths.ovcli_config, "http://127.0.0.1:1938", json.loads(paths.server_config.read_text())
    )
    new_vlm = {
        "provider": "openai",
        "model": "new-model",
        "api_key": "new-secret",
        "api_base": "https://new.example/v1",
    }
    resolve = MagicMock(return_value=new_vlm)
    monkeypatch.setattr(quick_local, "resolve_hermes_vlm_config", resolve)
    setup = quick_local.QuickLocalSetup(
        health_check=lambda _endpoint: (True, ""),
    )
    ensure_runtime = MagicMock(return_value=False)
    monkeypatch.setattr(setup, "_ensure_openviking_installed", ensure_runtime)
    monkeypatch.setattr(quick_local, "find_available_port", lambda **_kwargs: 1940)
    validate = MagicMock()
    monkeypatch.setattr(setup, "_validate_generated_config", validate)

    def restart(paths, endpoint):
        quick_local.clear_server_restart_required(paths.server_config)
        return endpoint

    restart = MagicMock(side_effect=restart)
    monkeypatch.setattr(setup, "_start_managed_server", restart)

    result = setup.provision(hermes_home=tmp_path)

    assert result.reused is True
    assert result.endpoint == "http://127.0.0.1:1938"
    assert result.server_restart_required is False
    validate.assert_called_once()
    restart.assert_called_once_with(paths, "http://127.0.0.1:1938")
    assert json.loads(paths.server_config.read_text(encoding="utf-8")) == (
        quick_local.build_server_config(paths, new_vlm, port=1938)
    )
    resolve.assert_called_once_with()
    ensure_runtime.assert_called_once_with(paths)


def test_failed_restart_is_retried_on_next_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(quick_local, "find_available_port", lambda **_kwargs: 1940)
    monkeypatch.setattr(quick_local, "server_belongs_to_profile", lambda *_args: True)
    lifecycle = importlib.import_module(quick_local.__package__ + ".local_server")
    monkeypatch.setattr(lifecycle.LocalServer, "_verified_process", lambda *_args: object())
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir(parents=True)
    old_vlm = {
        "provider": "openai",
        "model": "old-model",
        "api_key": "old-secret",
        "api_base": "https://llm.example/v1",
    }
    new_vlm = {**old_vlm, "api_key": "new-secret"}
    quick_local.atomic_json_write(
        paths.server_config,
        quick_local.build_server_config(paths, old_vlm, port=1938),
        mode=0o600,
    )
    quick_local._write_ovcli_profile(
        paths.ovcli_config, "http://127.0.0.1:1938", json.loads(paths.server_config.read_text())
    )
    monkeypatch.setattr(quick_local, "resolve_hermes_vlm_config", lambda: new_vlm)

    def provision_again():
        setup = quick_local.QuickLocalSetup(health_check=lambda _endpoint: (True, ""))
        monkeypatch.setattr(setup, "_ensure_openviking_installed", lambda _paths: False)
        monkeypatch.setattr(setup, "_validate_generated_config", lambda **_kwargs: None)
        monkeypatch.setattr(
            setup,
            "_start_managed_server",
            MagicMock(side_effect=quick_local.QuickLocalSetupError("restart failed")),
        )
        return setup.provision(hermes_home=tmp_path)

    with pytest.raises(quick_local.QuickLocalSetupError, match="restart failed"):
        provision_again()
    with pytest.raises(quick_local.QuickLocalSetupError, match="restart failed"):
        provision_again()
    assert paths.restart_required_marker.is_file()


def test_fresh_provision_validates_before_writing_active_config(tmp_path, monkeypatch):
    events = []
    validation_configs = []
    process = MagicMock()
    setup = quick_local.QuickLocalSetup(
        health_check=lambda _endpoint: (True, ""),
        progress=lambda event: events.append(event.stage),
    )
    monkeypatch.setattr(
        quick_local,
        "resolve_hermes_vlm_config",
        lambda: {
            "provider": "openai",
            "model": "model-1",
            "api_key": "secret",
            "api_base": "https://llm.example/v1",
        },
    )

    def mark_ready(paths, endpoint):
        assert paths.restart_required_marker.is_file()
        quick_local.clear_server_restart_required(paths.server_config)
        return endpoint

    ready = MagicMock(side_effect=mark_ready)
    monkeypatch.setattr(setup, "_start_managed_server", ready)
    monkeypatch.setattr(setup, "_ensure_openviking_installed", lambda _paths: True)
    monkeypatch.setattr(quick_local, "find_available_port", lambda **_kwargs: 1937)
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir(parents=True)
    lifecycle = importlib.import_module(quick_local.__package__ + ".local_server")
    write_profile = quick_local._write_ovcli_profile

    def write_under_lock(*args):
        with pytest.raises(quick_local.QuickLocalSetupError, match="busy"):
            with lifecycle.LocalServer(tmp_path).locked(timeout=0):
                pass
        return write_profile(*args)

    monkeypatch.setattr(quick_local, "_write_ovcli_profile", write_under_lock)
    quick_local.atomic_json_write(
        paths.restart_required_marker,
        {"restart_required": True},
        mode=0o600,
    )

    def start(endpoint, config_path, hermes_home, server_command):
        assert endpoint == "http://127.0.0.1:1937"
        assert hermes_home == tmp_path
        assert server_command == paths.server_command
        assert not paths.server_config.exists()
        assert not paths.ovcli_config.exists()
        validation_configs.append(json.loads(config_path.read_text(encoding="utf-8")))
        return process

    monkeypatch.setattr(quick_local, "_start_validation_server", start)
    monkeypatch.setattr(quick_local, "_wait_for_health", lambda *args, **kwargs: True)
    monkeypatch.setattr(quick_local, "_validate_tenant_access", lambda *_args: None)
    stop = MagicMock(return_value=True)
    monkeypatch.setattr(quick_local, "_stop_process", stop)

    result = setup.provision(
        hermes_home=tmp_path,
        preflight=_preflight(tmp_path),
    )

    paths = result.paths
    assert result.endpoint == "http://127.0.0.1:1937"
    assert result.reused is False
    ready.assert_called_once_with(paths, "http://127.0.0.1:1937")
    assert result.server_restart_required is False
    assert not paths.restart_required_marker.exists()
    final_config = json.loads(paths.server_config.read_text(encoding="utf-8"))
    assert final_config["storage"]["workspace"] == str(paths.workspace)
    assert final_config["server"]["port"] == 1937
    assert final_config["embedding"]["dense"]["cache_dir"] == str(paths.model_cache)
    assert validation_configs[0]["storage"]["workspace"] != str(paths.workspace)
    assert validation_configs[0]["embedding"]["dense"]["cache_dir"] == str(paths.model_cache)
    assert json.loads(paths.ovcli_config.read_text(encoding="utf-8")) == {
        "url": "http://127.0.0.1:1937",
        "actor_peer_id": "hermes",
        "root_api_key": "local-test-key",
        "account": "default",
        "user": "default",
    }
    if os.name != "nt":
        assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(paths.model_cache.stat().st_mode) == 0o700
        assert stat.S_IMODE(paths.server_config.stat().st_mode) == 0o600
        assert stat.S_IMODE(paths.ovcli_config.stat().st_mode) == 0o600
    stop.assert_called_once_with(process)
    assert quick_local.QuickLocalStage.PREPARE_EMBEDDING in events
    assert quick_local.QuickLocalStage.VALIDATE in events
    assert events[-2:] == [
        quick_local.QuickLocalStage.WRITE_CONFIG,
        quick_local.QuickLocalStage.COMPLETE,
    ]


def test_fresh_provision_failed_start_keeps_restart_marker(tmp_path, monkeypatch):
    setup = quick_local.QuickLocalSetup(health_check=lambda _endpoint: (False, ""))
    monkeypatch.setattr(quick_local, "resolve_hermes_vlm_config", lambda: {"model": "test"})
    monkeypatch.setattr(quick_local, "find_available_port", lambda **_kwargs: 1937)
    monkeypatch.setattr(setup, "_ensure_openviking_installed", lambda _paths: True)
    monkeypatch.setattr(setup, "_validate_generated_config", lambda **_kwargs: None)
    monkeypatch.setattr(
        setup,
        "_start_managed_server",
        MagicMock(side_effect=quick_local.QuickLocalSetupError("startup failed")),
    )
    with pytest.raises(quick_local.QuickLocalSetupError, match="startup failed"):
        setup.provision(hermes_home=tmp_path, preflight=_preflight(tmp_path))
    paths = quick_local.managed_paths(tmp_path)
    assert paths.restart_required_marker.is_file()
    assert quick_local.connection_config({}, tmp_path)["url"] == "http://127.0.0.1:1937"


def test_failed_validation_stops_child_and_leaves_profile_inactive(tmp_path, monkeypatch):
    process = MagicMock()
    process.poll.return_value = None
    setup = quick_local.QuickLocalSetup(health_check=lambda _endpoint: (False, "down"))
    monkeypatch.setattr(
        quick_local,
        "resolve_hermes_vlm_config",
        lambda: {
            "provider": "openai",
            "model": "model-1",
            "api_key": "secret",
            "api_base": "https://llm.example/v1",
        },
    )
    monkeypatch.setattr(setup, "_ensure_openviking_installed", lambda _paths: True)
    monkeypatch.setattr(quick_local, "find_available_port", lambda **_kwargs: 1933)
    monkeypatch.setattr(
        quick_local,
        "_start_validation_server",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(quick_local, "_wait_for_health", lambda *args, **kwargs: False)
    stop = MagicMock(return_value=True)
    monkeypatch.setattr(quick_local, "_stop_process", stop)

    with pytest.raises(quick_local.QuickLocalSetupError, match="did not become reachable"):
        setup.provision(
            hermes_home=tmp_path,
            preflight=_preflight(tmp_path),
        )

    paths = quick_local.managed_paths(tmp_path)
    assert not paths.server_config.exists()
    assert not paths.ovcli_config.exists()
    stop.assert_called_once_with(process)


def test_validation_preserves_primary_error_when_cleanup_also_fails(tmp_path, monkeypatch):
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir(parents=True)
    process = MagicMock()
    process.poll.return_value = None
    setup = quick_local.QuickLocalSetup(health_check=lambda _endpoint: (False, "down"))
    monkeypatch.setattr(
        quick_local,
        "_start_validation_server",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(quick_local, "_wait_for_health", lambda *args, **kwargs: False)
    monkeypatch.setattr(quick_local, "_stop_process", lambda _process: False)

    with pytest.raises(
        quick_local.QuickLocalSetupError,
        match="did not become reachable",
    ) as exc_info:
        setup._validate_generated_config(
            paths=paths,
            endpoint="http://127.0.0.1:1933",
            server_config=quick_local.build_server_config(
                paths,
                {
                    "provider": "openai",
                    "model": "model-1",
                    "api_key": "secret",
                    "api_base": "https://llm.example/v1",
                },
            ),
        )

    assert any("could not be stopped" in note for note in getattr(exc_info.value, "__notes__", []))


def test_preflight_reuses_only_healthy_profile_with_managed_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(quick_local, "server_belongs_to_profile", lambda *_args: True)
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir(parents=True)
    quick_local.atomic_json_write(
        paths.server_config,
        {"storage": {"workspace": str(paths.workspace)}},
        mode=0o600,
    )
    quick_local.atomic_json_write(
        paths.ovcli_config,
        {"url": "http://127.0.0.1:1938", "actor_peer_id": "hermes"},
        mode=0o600,
    )
    health = MagicMock(return_value=(True, ""))
    setup = quick_local.QuickLocalSetup(health_check=health)

    preflight = setup.preflight(tmp_path)

    assert preflight.reusable_endpoint == "http://127.0.0.1:1938"
    health.assert_called_once_with("http://127.0.0.1:1938")

    quick_local.atomic_json_write(
        paths.server_config,
        {"storage": {"workspace": str(tmp_path / "other-data")}},
        mode=0o600,
    )
    health.reset_mock()
    assert setup.preflight(tmp_path).reusable_endpoint is None
    health.assert_not_called()


def test_preferred_configured_port_is_reused_when_available(monkeypatch):
    bound = []

    class FakeSocket:
        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            return 1

        def setsockopt(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def bind(self, address):
            bound.append(address)

    monkeypatch.setattr(quick_local.socket, "socket", lambda *args, **kwargs: FakeSocket())

    port = quick_local.find_available_port(
        preferred_endpoint="http://127.0.0.1:1940",
    )

    assert port == 1940
    assert bound == [("127.0.0.1", 1940)]


def test_validation_stop_force_kills_only_after_graceful_timeout(monkeypatch):
    from itertools import count

    import psutil

    process = MagicMock()
    process.poll.return_value = None
    root, child = MagicMock(), MagicMock()
    root.children.return_value = [child]
    root.status.return_value = psutil.STATUS_RUNNING
    child.is_running.return_value = False
    root.is_running.return_value = True
    root.kill.side_effect = lambda: setattr(root.is_running, "return_value", False)
    monkeypatch.setattr(psutil, "Process", lambda _pid: root)
    lifecycle = importlib.import_module(quick_local.__package__ + ".local_server")
    clock = count(step=quick_local._PROCESS_STOP_TIMEOUT_SECONDS)
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: next(clock))

    assert quick_local._stop_process(process) is True

    root.terminate.assert_called_once_with()
    child.terminate.assert_called_once_with()
    root.kill.assert_called_once_with()
    child.kill.assert_not_called()
    process.wait.assert_called_once_with(timeout=quick_local._PROCESS_STOP_TIMEOUT_SECONDS)


def test_health_wait_budget_covers_openviking_model_download():
    assert quick_local._HEALTH_TIMEOUT_SECONDS > quick_local._MODEL_PREPARATION_TIMEOUT_SECONDS


def test_health_wait_stops_as_soon_as_validation_process_exits():
    process = MagicMock()
    process.poll.return_value = 1
    health = MagicMock(return_value=(False, "down"))

    assert (
        quick_local._wait_for_health(
            "http://127.0.0.1:1933",
            health,
            process=process,
            timeout_seconds=60,
        )
        is False
    )

    health.assert_called_once_with("http://127.0.0.1:1933")


def _saved_local(home, *, port=1938):
    paths = quick_local.managed_paths(home)
    paths.root.mkdir(parents=True, exist_ok=True)
    config = quick_local.build_server_config(
        paths,
        {
            "provider": "openai",
            "model": "test-model",
            "api_key": "vlm-key",
            "api_base": "https://llm.example/v1",
        },
        port=port,
    )
    quick_local.atomic_json_write(paths.server_config, config, mode=0o600)
    quick_local._write_ovcli_profile(paths.ovcli_config, f"http://127.0.0.1:{port}", config)
    return paths, config


@pytest.mark.parametrize(
    "mode,code,accepted", [("trusted", 200, True), ("trusted", 403, False), ("dev", 200, False)]
)
def test_reuse_authenticates_profile_key(tmp_path, monkeypatch, mode, code, accepted):
    paths, _cfg = _saved_local(tmp_path)
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/health":
            assert "X-API-Key" not in request.headers
            return httpx.Response(
                200, json={"status": "ok", "auth_mode": mode, "root_api_key_required": True}
            )
        assert request.url.path == "/api/v1/admin/accounts"
        assert request.headers["X-API-Key"] == "local-test-key"
        return httpx.Response(code, json={"status": "ok" if code == 200 else "error"})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)
    )
    assert quick_local.server_belongs_to_profile(paths, "http://127.0.0.1:1938") is accepted
    assert len(requests) == (1 if mode == "dev" else 2)


def test_wrong_listener_is_not_reused(tmp_path, monkeypatch):
    paths, _cfg = _saved_local(tmp_path)
    monkeypatch.setattr(quick_local, "server_belongs_to_profile", lambda *_args: False)
    assert quick_local.find_reusable_endpoint(paths, lambda _url: (True, "")) is None


def test_current_installer_uses_pm_and_private_root(tmp_path, monkeypatch):
    pm = pytest.importorskip("pm.client")
    install = MagicMock()
    monkeypatch.setattr(pm, "ensure_python_tool", install)
    satisfies = MagicMock(side_effect=[False, True])
    monkeypatch.setattr(quick_local, "openviking_install_satisfies_requirement", satisfies)
    paths = quick_local.managed_paths(tmp_path)
    packages = importlib.import_module(quick_local.__package__ + ".local_packages")
    monkeypatch.setattr(
        packages, "verified_requirements", lambda requirements, _cache: requirements
    )
    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (False, ""))
    assert engine._ensure_openviking_installed(paths)
    requirements = packages.install_requirements()
    requirements[0] = packages.resolve_server_requirement()
    install.assert_called_once_with(
        "openviking-local",
        requirements,
        "openviking-server",
        root=paths.runtime,
        explicit=True,
        timeout=1800,
    )


def test_pm_failure_does_not_activate_profile(tmp_path, monkeypatch):
    pm = pytest.importorskip("pm.client")
    monkeypatch.setattr(
        pm, "ensure_python_tool", MagicMock(side_effect=RuntimeError("install failed"))
    )
    monkeypatch.setattr(
        quick_local, "openviking_install_satisfies_requirement", lambda _paths: False
    )
    packages = importlib.import_module(quick_local.__package__ + ".local_packages")
    monkeypatch.setattr(
        packages, "verified_requirements", lambda requirements, _cache: requirements
    )
    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (False, ""))
    with pytest.raises(quick_local.QuickLocalSetupError, match="private OpenViking runtime"):
        engine._ensure_openviking_installed(quick_local.managed_paths(tmp_path))
    assert not quick_local.managed_paths(tmp_path).ovcli_config.exists()


def test_compatible_runtime_avoids_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        quick_local, "openviking_install_satisfies_requirement", lambda _paths: True
    )
    run = MagicMock()
    monkeypatch.setattr(quick_local.subprocess, "run", run)
    packages = importlib.import_module(quick_local.__package__ + ".local_packages")
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir()
    requirements = packages.install_requirements()
    requirements[0] = packages.resolve_server_requirement()
    (paths.root / "runtime-requirements.json").write_text(json.dumps(requirements))
    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (False, ""))
    assert engine._ensure_openviking_installed(quick_local.managed_paths(tmp_path)) is False
    run.assert_not_called()


def test_setup_checks_new_release_even_with_a_compatible_installed_runtime(tmp_path, monkeypatch):
    pm = pytest.importorskip("pm.client")
    packages = importlib.import_module(quick_local.__package__ + ".local_packages")
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir()
    requirements = packages.install_requirements()
    requirements[0] = "openviking[local-embed]==0.4.22"
    (paths.root / "runtime-requirements.json").write_text(json.dumps(requirements))
    monkeypatch.setattr(quick_local, "openviking_install_satisfies_requirement", lambda _p: True)
    monkeypatch.setattr(packages, "verified_requirements", lambda values, _cache: values)
    install = MagicMock()
    monkeypatch.setattr(pm, "ensure_python_tool", install)

    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (False, ""))
    assert engine._ensure_openviking_installed(paths) is True
    assert install.call_args.args[1][0] == "openviking[local-embed]==0.4.23"
    assert json.loads((paths.root / "runtime-requirements.json").read_text())[0] == (
        "openviking[local-embed]==0.4.23"
    )


def test_release_check_failure_preserves_runtime_receipt(tmp_path, monkeypatch):
    packages = importlib.import_module(quick_local.__package__ + ".local_packages")
    paths = quick_local.managed_paths(tmp_path)
    paths.root.mkdir()
    receipt = paths.root / "runtime-requirements.json"
    receipt.write_text('["previous requirements"]')
    monkeypatch.setattr(
        packages, "resolve_server_requirement",
        MagicMock(side_effect=quick_local.QuickLocalSetupError("PyPI unavailable")),
    )
    run = MagicMock()
    monkeypatch.setattr(quick_local.subprocess, "run", run)
    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (False, ""))
    with pytest.raises(quick_local.QuickLocalSetupError, match="PyPI unavailable"):
        engine._ensure_openviking_installed(paths)
    assert receipt.read_text() == '["previous requirements"]'
    run.assert_not_called()


def test_managed_connection_ignores_foreign_env_and_saved_paths(tmp_path, monkeypatch):
    paths, _cfg = _saved_local(tmp_path)
    monkeypatch.setattr(openviking_module, "get_hermes_home", lambda: tmp_path)
    settings = openviking_module._resolve_connection_settings(
        {"deployment": quick_local.DEPLOYMENT, "ovcli_config_path": "/foreign/ovcli.conf"},
        env={
            "OPENVIKING_ENDPOINT": "https://foreign.example",
            "OPENVIKING_API_KEY": "foreign-key",
            "OVCLI_CONFIG": "/foreign/ovcli.conf",
        },
    )
    assert settings == {
        "endpoint": "http://127.0.0.1:1938",
        "api_key": "local-test-key",
        "account": "default",
        "user": "default",
        "agent": "hermes",
    }
    assert (
        openviking_module._provider_ovcli_config_path({"deployment": quick_local.DEPLOYMENT})
        == paths.ovcli_config
    )


def test_copied_profile_cannot_use_source_workspace(tmp_path):
    import shutil

    home_a, home_b = tmp_path / "a", tmp_path / "b"
    paths, _cfg = _saved_local(home_a)
    shutil.copytree(paths.root, home_b / "openviking")
    with pytest.raises(quick_local.QuickLocalSetupError, match="another profile"):
        quick_local.connection_config({"deployment": quick_local.DEPLOYMENT}, home_b)


def test_managed_symlink_is_rejected(tmp_path):
    destination = tmp_path / "other-profile"
    destination.mkdir()
    (tmp_path / "openviking").symlink_to(destination, target_is_directory=True)
    with pytest.raises(quick_local.QuickLocalSetupError, match="belong to this Hermes profile"):
        quick_local.managed_paths(tmp_path)


def test_backup_skips_managed_profile_but_keeps_external_file(tmp_path, monkeypatch):
    provider = OpenVikingMemoryProvider()
    provider._hermes_home = str(tmp_path)
    config = {"deployment": quick_local.DEPLOYMENT, "use_ovcli_config": True}
    monkeypatch.setattr(provider, "_profile_config_and_env", lambda: (config, {}))
    assert provider.backup_paths() == []
    linked = tmp_path.parent / "outside-ovcli.conf"
    config.clear()
    config.update(use_ovcli_config=True, ovcli_config_path=str(linked))
    assert provider.backup_paths() == [str(linked)]


def test_running_managed_server_recovers_after_config_change(tmp_path, monkeypatch):
    paths, _cfg = _saved_local(tmp_path)
    quick_local._mark_server_restart_required(paths)
    provider = OpenVikingMemoryProvider()
    provider._hermes_home = str(tmp_path)
    monkeypatch.setattr(
        provider, "_profile_config_and_env", lambda: ({"deployment": quick_local.DEPLOYMENT}, {})
    )
    monkeypatch.setattr(openviking_module, "_local_openviking_port_is_open", lambda *_args: True)
    provider._env_refresh_enabled = True
    build = MagicMock()
    monkeypatch.setattr(provider, "_build_client", build)
    restart = MagicMock()
    monkeypatch.setattr(provider, "_handle_runtime_openviking_unreachable", restart)
    assert provider._ensure_client() is None
    restart.assert_called_once()
    build.assert_not_called()
    assert paths.restart_required_marker.exists()


def test_quick_local_wizard_links_only_after_success(tmp_path, monkeypatch, capsys):
    paths, _cfg = _saved_local(tmp_path)
    result = quick_local.QuickLocalSetupResult(paths, "http://127.0.0.1:1938", True)
    engine = MagicMock()
    engine.provision.return_value = result
    monkeypatch.setattr(quick_local, "QuickLocalSetup", lambda **_kwargs: engine)
    config = {"memory": {}}
    provider_config = {"recall_scope": "peer"}
    assert setup_flow._run_quick_local_setup(
        config=config, provider_config=provider_config, env_path=tmp_path / ".env"
    )
    assert provider_config["deployment"] == quick_local.DEPLOYMENT
    assert provider_config["ovcli_config_path"] == str(paths.ovcli_config)
    assert "server_command_path" not in provider_config
    assert "OpenViking memory is ready" in capsys.readouterr().out
    engine.provision.side_effect = quick_local.QuickLocalSetupError("bad configuration")
    previous = dict(provider_config)
    assert (
        setup_flow._run_quick_local_setup(
            config=config, provider_config=provider_config, env_path=tmp_path / ".env"
        )
        is False
    )
    assert provider_config == previous


def test_cloud_custom_menu_positions_are_unchanged(monkeypatch):
    seen = []

    def select(_title, options, **_kwargs):
        seen.extend(options)
        return -1

    result = setup_flow._run_create_profile_setup(
        prompt=MagicMock(),
        select=select,
        cancelled=-1,
        config={},
        provider_config={},
        env_path=Path("unused"),
    )
    assert result is setup_flow._SETUP_CANCELLED
    assert [option[0] for option in seen] == [
        "OpenViking Service (VolcEngine Cloud)",
        "Custom",
        "Quick Local",
    ]


def test_switch_to_custom_clears_managed_marker(tmp_path):
    config = {"memory": {}}
    provider_config = {
        "deployment": quick_local.DEPLOYMENT,
        "server_config_path": "old",
        "server_command_path": "old",
    }
    setup_flow._save_hermes_only_config(
        config=config,
        provider_config=provider_config,
        env_path=tmp_path / ".env",
        values={"endpoint": "https://example.com", "api_key": "new-key"},
    )
    assert not any(
        key in provider_config
        for key in ("deployment", "server_config_path", "server_command_path")
    )
    assert provider_config["use_ovcli_config"] is False


@pytest.mark.parametrize("status,accepted", [(200, True), (403, False)])
def test_validation_checks_tenant_write_access(monkeypatch, status, accepted):
    def post(url, **kwargs):
        assert url == "http://127.0.0.1:1938/api/v1/sessions"
        assert kwargs["headers"]["X-OpenViking-Account"] == "default"
        assert kwargs["headers"]["X-OpenViking-User"] == "default"
        assert kwargs["headers"]["X-API-Key"] == "local-test-key"
        assert kwargs["json"] == {} and kwargs["trust_env"] is False
        return httpx.Response(
            status,
            json={"status": "ok" if accepted else "error"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    if accepted:
        quick_local._validate_tenant_access("http://127.0.0.1:1938", "local-test-key")
    else:
        with pytest.raises(quick_local.QuickLocalSetupError, match="memory data access"):
            quick_local._validate_tenant_access("http://127.0.0.1:1938", "local-test-key")


def test_failed_data_validation_does_not_activate_configuration(tmp_path, monkeypatch):
    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (True, ""))
    monkeypatch.setattr(
        quick_local,
        "resolve_hermes_vlm_config",
        lambda: {
            "provider": "openai",
            "model": "test",
            "api_key": "key",
            "api_base": "https://llm.example/v1",
        },
    )
    monkeypatch.setattr(engine, "_ensure_openviking_installed", lambda _paths: False)
    monkeypatch.setattr(quick_local, "find_available_port", lambda **_kwargs: 1938)
    process = MagicMock()
    monkeypatch.setattr(quick_local, "_start_validation_server", lambda *_args: process)
    monkeypatch.setattr(quick_local, "_wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        quick_local,
        "_validate_tenant_access",
        MagicMock(side_effect=quick_local.QuickLocalSetupError("memory data access denied")),
    )
    stop = MagicMock(return_value=True)
    monkeypatch.setattr(quick_local, "_stop_process", stop)
    with pytest.raises(quick_local.QuickLocalSetupError, match="memory data access"):
        engine.provision(hermes_home=tmp_path)
    paths = quick_local.managed_paths(tmp_path)
    assert not paths.server_config.exists() and not paths.ovcli_config.exists()
    stop.assert_called_once_with(process)


@pytest.mark.parametrize("healthy", [True, False])
def test_managed_start_keeps_only_a_ready_process(tmp_path, monkeypatch, healthy):
    paths, _cfg = _saved_local(tmp_path)
    process = MagicMock()
    lifecycle = importlib.import_module(quick_local.__package__ + ".local_server")
    server = MagicMock()
    started = lifecycle.StartedServer("http://127.0.0.1:1938", process, False)
    server.start.return_value = started
    if healthy:
        server.wait_ready.return_value = started
    else:
        server.wait_ready.side_effect = quick_local.QuickLocalSetupError("did not become ready")
    monkeypatch.setattr(lifecycle, "LocalServer", lambda _home: server)
    engine = quick_local.QuickLocalSetup(health_check=lambda _url: (True, ""))
    if healthy:
        engine._start_managed_server(paths, "http://127.0.0.1:1938")
    else:
        with pytest.raises(quick_local.QuickLocalSetupError, match="did not become ready"):
            engine._start_managed_server(paths, "http://127.0.0.1:1938")
    server.wait_ready.assert_called_once_with(started, engine._health_check)


def test_managed_initialize_preserves_cli_startup_callbacks(tmp_path, monkeypatch):
    provider = OpenVikingMemoryProvider()
    monkeypatch.setattr(
        provider, "_profile_config_and_env", lambda: ({"deployment": quick_local.DEPLOYMENT}, {})
    )
    monkeypatch.setattr(
        provider,
        "_resolve_bound_connection_settings",
        lambda *_args: {
            "endpoint": "http://127.0.0.1:1938",
            "api_key": "key",
            "account": "default",
            "user": "default",
            "agent": "hermes",
        },
    )
    ensure = MagicMock(return_value=None)
    monkeypatch.setattr(provider, "_ensure_client_locked", ensure)
    status, warning = MagicMock(), MagicMock()
    try:
        provider.initialize(
            "sid",
            hermes_home=str(tmp_path),
            platform="cli",
            status_callback=status,
            warning_callback=warning,
        )
        ensure.assert_called_once_with(status_callback=status, warning_callback=warning)
    finally:
        provider.shutdown()


def test_managed_start_timeout_reports_its_actual_budget(tmp_path, monkeypatch):
    provider = OpenVikingMemoryProvider()
    provider._hermes_home = str(tmp_path)
    provider._endpoint = "http://127.0.0.1:1938"
    monkeypatch.setattr(
        provider, "_profile_config_and_env", lambda: ({"deployment": quick_local.DEPLOYMENT}, {})
    )
    wait = MagicMock(return_value=False)
    monkeypatch.setattr(openviking_module, "_wait_for_openviking_health", wait)
    warning = MagicMock()
    provider._finish_runtime_openviking_start(warning_callback=warning)
    assert wait.call_args.kwargs["timeout_seconds"] == quick_local._HEALTH_TIMEOUT_SECONDS
    assert f"{quick_local._HEALTH_TIMEOUT_SECONDS:.0f} seconds" in warning.call_args.args[0]
