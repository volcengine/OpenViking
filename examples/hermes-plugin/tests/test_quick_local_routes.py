"""Copied LLM routes must match the transport the private server can use."""

import importlib
import os
import subprocess
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def ql(external_provider):
    _home, _provider, module, _settings = external_provider("routes")
    return importlib.import_module(module.__name__ + ".quick_local")


def resolve(ql, monkeypatch, **overrides):
    from hermes_cli import config, runtime_provider

    runtime = {
        "provider": "custom", "source": "custom_provider:test", "api_key": "static-key",
        "model": "test-model", "base_url": "https://proxy.example/anthropic/v1",
        "api_mode": "anthropic_messages",
        **overrides,
    }
    monkeypatch.setattr(config, "load_config", lambda: {"model": {"default": runtime["model"]}})
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_kw: runtime)
    return ql.resolve_hermes_vlm_config()


@pytest.mark.parametrize("suffix", ["", "/v1beta", "/v1beta/", "/v1", "/v1alpha"])
def test_native_gemini_uses_openai_compatible_route(ql, monkeypatch, suffix):
    vlm = resolve(ql, monkeypatch, provider="gemini", source="env:GEMINI_API_KEY",
                  api_mode="chat_completions", model="gemini-test",
                  base_url="https://generativelanguage.googleapis.com" + suffix)
    assert vlm["provider"] == "openai"
    assert vlm["api_base"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert vlm["model"] == "gemini-test"


def test_explicit_gemini_openai_route_is_retained(ql, monkeypatch):
    base = "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert resolve(ql, monkeypatch, api_mode="chat_completions", base_url=base)["api_base"] == base
    proxy = "https://proxy.example/v1beta"
    assert resolve(ql, monkeypatch, api_mode="chat_completions", base_url=proxy)["api_base"] == proxy


@pytest.mark.parametrize("base", ["https://api.minimax.io/anthropic", "https://api.minimaxi.com/anthropic",
                                  "https://res.services.ai.azure.com/anthropic"])
def test_bearer_anthropic_routes_fail_before_copying(ql, monkeypatch, base):
    with pytest.raises(ql.QuickLocalSetupError, match="cannot reproduce authentication"):
        resolve(ql, monkeypatch, base_url=base)


def test_oauth_shaped_proxy_key_is_not_reinterpreted_by_litellm(ql, monkeypatch):
    with pytest.raises(ql.QuickLocalSetupError, match="cannot reproduce authentication"):
        resolve(ql, monkeypatch, api_key="sk-ant-oat-proxy-static")


def test_kimi_coding_uses_hermes_attribution(ql, monkeypatch):
    vlm = resolve(ql, monkeypatch, base_url="https://api.kimi.com/coding",
                  extra_headers={"X-Custom": "kept"})
    assert vlm["extra_headers"]["User-Agent"].startswith("HermesAgent/")
    assert vlm["extra_headers"]["X-Custom"] == "kept"
    assert vlm["api_key"] == "static-key"


@pytest.mark.parametrize("provider,base,key_env", [
    ("openai-api", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ("xai", "https://api.x.ai/v1", "XAI_API_KEY"),
])
def test_official_static_responses_route_can_use_chat_completions(ql, monkeypatch, provider, base, key_env):
    vlm = resolve(ql, monkeypatch, provider=provider, source="env:" + key_env,
                  api_mode="codex_responses", base_url=base)
    assert vlm["provider"] == "openai" and vlm["api_base"] == base


def test_other_responses_routes_are_not_guessed(ql, monkeypatch):
    with pytest.raises(ql.QuickLocalSetupError, match="transport is not supported"):
        resolve(ql, monkeypatch, api_mode="codex_responses", base_url="https://proxy.example/v1")


@pytest.mark.parametrize("failure", [1, subprocess.TimeoutExpired("llm", 90)])
def test_llm_access_failure_does_not_activate_setup(ql, tmp_path, monkeypatch, failure):
    paths = ql.managed_paths(tmp_path)
    start = MagicMock()
    monkeypatch.setattr(ql, "_start_validation_server", start)
    run = MagicMock(side_effect=failure if isinstance(failure, Exception) else None,
                    return_value=MagicMock(returncode=failure))
    monkeypatch.setattr(ql.subprocess, "run", run)
    with pytest.raises(ql.QuickLocalSetupError, match="LLM.*check"):
        ql.QuickLocalSetup(health_check=lambda _url: (True, ""))._validate_generated_config(
            paths=paths, endpoint="http://127.0.0.1:1934",
            server_config=ql.build_server_config(paths, {"model": "test-model"}),
        )
    start.assert_not_called()
    assert not paths.server_config.exists() and not paths.ovcli_config.exists()
    assert not paths.restart_required_marker.exists()


def test_named_legacy_profile_finds_root_uv_without_repair(ql, tmp_path, monkeypatch):
    from hermes_cli import managed_uv

    profile = tmp_path / "profiles/work"
    profile.mkdir(parents=True)
    uv = tmp_path / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    uv.parent.mkdir()
    uv.touch()
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setattr(managed_uv, "resolve_uv", lambda: None)
    monkeypatch.setattr(ql.shutil, "which", lambda _name: None)
    repair = MagicMock(side_effect=AssertionError("must not repair the host"))
    monkeypatch.setattr(managed_uv, "ensure_uv", repair)
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(ql.subprocess, "run", run)
    ql.QuickLocalSetup(health_check=lambda _url: (False, ""))._install_with_uv(
        ql.managed_paths(profile), ["isolated-package"]
    )
    assert all(call.args[0][0] == str(uv) for call in run.call_args_list)
    repair.assert_not_called()
