"""Exercise external Desktop setup without a bundled provider or TUI prompts."""

import importlib
import json
import os
from contextlib import contextmanager

import pytest
import yaml
from plugins.memory import config_schema


@contextmanager
def scope(home):
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    secret = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
    try:
        yield
    finally:
        reset_secret_scope(secret)
        reset_hermes_home_override(token)


@pytest.mark.parametrize("mode", ["custom", "profile", "service"])
def test_desktop_activation_preserves_profile_boundaries_and_secrets(
    external_provider, monkeypatch, mode
):
    if not getattr(config_schema, "PROVIDER_SETUP_API_VERSION", 0):
        pytest.skip("Desktop setup requires the optional Hermes managed-setup capability")
    home, provider, module, _ = external_provider("desktop-a")
    other, other_provider, _, _ = external_provider("desktop-b")
    unchanged = (other / "config.yaml").read_bytes()
    original_env = dict(os.environ)
    (home / ".env").write_text(
        "UNRELATED=keep\nOPENVIKING_API_KEY=old-key\nOPENVIKING_RECALL_SCOPE=shared\n"
    )
    (home / "config.yaml").write_text(
        "model:\n  default: unchanged\nmemory:\n  provider: openviking\n  openviking:\n    recall_limit: 9\n"
    )
    desktop = importlib.import_module(module.__name__ + "._desktop")
    monkeypatch.setattr(
        module, "_validate_openviking_setup_values", lambda *a, **k: (True, "OK", "user")
    )
    values = {
        "setup_type": mode,
        "usage_profile": "personal",
        "profile_name": "desktop-test",
        "url": "http://127.0.0.1:1933",
        "credential": "user",
        "api_key": "new-key",
        "api_key_service": "new-key",
    }
    linked = module._default_ovcli_config_path().parent / "ovcli.conf.linked"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.write_text(json.dumps({"url": "http://127.0.0.1:1933", "api_key": "linked-key"}))
    values["profile_path"] = str(linked)
    with scope(home):
        schema = config_schema.get_provider_config_schema("openviking")
        assert schema.submit_action == "save"
        result = provider.handle_desktop_config_action(
            "save", {"values": values}, hermes_home=str(home)
        )
        assert result["ok"]
        snap = provider.get_desktop_config(hermes_home=str(home))
        assert all(key not in json.dumps(snap) for key in ("new-key", "old-key", "linked-key"))
        data = yaml.safe_load((home / "config.yaml").read_text())
        assert data["model"]["default"] == "unchanged"
        assert data["memory"]["openviking"]["recall_limit"] == 9
        assert data["memory"]["openviking"]["recall_scope"] == "peer"
        assert "api_key" not in data["memory"]["openviking"]
        assert "OPENVIKING_API_KEY" not in (home / ".env").read_text()
        assert "OPENVIKING_RECALL_SCOPE" not in (home / ".env").read_text()
        assert "UNRELATED=keep" in (home / ".env").read_text()
        if mode != "profile":
            assert os.stat(result["profile_path"]).st_mode & 0o777 == 0o600
        # Read the same setup with the other profile ambient; explicit home wins.
    with scope(other):
        assert (
            provider.get_desktop_config(hermes_home=str(home))["values"]["profile_path"]
            == result["profile_path"]
        )
        assert (
            other_provider.get_desktop_config(hermes_home=str(other))["values"]["profile_path"]
            == ""
        )
    assert (other / "config.yaml").read_bytes() == unchanged
    assert dict(os.environ) == original_env
    # Hidden secret input is discarded by backend normalization, not only by UI.
    keyless, _ = desktop.connection_values({**values, "setup_type": "custom", "credential": "none"})
    assert keyless["api_key"] == keyless["root_api_key"] == ""


def test_desktop_confirmation_validation_and_old_host_fallback(external_provider, monkeypatch):
    home, provider, module, _ = external_provider("desktop-confirm")
    if not getattr(config_schema, "PROVIDER_SETUP_API_VERSION", 0):
        with scope(home):
            assert config_schema.get_provider_config_schema("openviking") is None
        return
    from agent.memory_provider import MemoryProviderConfigConflictError

    before = (home / "config.yaml").read_bytes()
    desktop = importlib.import_module(module.__name__ + "._desktop")
    monkeypatch.setattr(
        module,
        "_validate_openviking_setup_values",
        lambda *a, **k: (False, "Rejected credentials", None),
    )
    values = {
        "setup_type": "custom",
        "usage_profile": "shared",
        "url": "http://127.0.0.1:1933",
        "credential": "none",
        "profile_name": "test",
    }
    with scope(home):
        with pytest.raises(MemoryProviderConfigConflictError) as exc:
            desktop.save(values=values, hermes_home=home, overwrite=False, confirmations={})
        assert exc.value.confirmation == "shared"
        with pytest.raises(ValueError, match="Rejected credentials"):
            desktop.save(
                values=values, hermes_home=home, overwrite=False, confirmations={"shared": True}
            )
    assert (home / "config.yaml").read_bytes() == before


def test_desktop_profile_collision_requires_confirmation(external_provider, monkeypatch):
    if not getattr(config_schema, "PROVIDER_SETUP_API_VERSION", 0):
        pytest.skip("Optional Desktop setup capability is absent")
    from agent.memory_provider import MemoryProviderConfigConflictError

    home, provider, module, _ = external_provider("collision")
    monkeypatch.setattr(
        module, "_validate_openviking_setup_values", lambda *a, **k: (True, "OK", None)
    )
    values = {
        "setup_type": "custom",
        "usage_profile": "personal",
        "url": "http://127.0.0.1:1933",
        "credential": "none",
        "profile_name": "same-name",
    }
    with scope(home):
        original = provider.handle_desktop_config_action(
            "save", {"values": values}, hermes_home=str(home)
        )
        before = (home / "config.yaml").read_bytes()
        path = module.Path(original["profile_path"])
        profile_before = path.read_bytes()
        values["url"] = "http://127.0.0.1:1934"
        with pytest.raises(MemoryProviderConfigConflictError):
            provider.handle_desktop_config_action("save", {"values": values}, hermes_home=str(home))
        assert (home / "config.yaml").read_bytes() == before
        assert path.read_bytes() == profile_before
        provider.handle_desktop_config_action(
            "save", {"values": values, "overwrite": True}, hermes_home=str(home)
        )
        assert json.loads(path.read_text())["url"] == values["url"]


def test_quick_local_failure_and_source_build_never_activate(external_provider, monkeypatch):
    if not getattr(config_schema, "PROVIDER_SETUP_API_VERSION", 0):
        pytest.skip("Optional Desktop setup capability is absent")
    from agent.memory_provider import MemoryProviderConfigConflictError

    home, provider, module, _ = external_provider("quick-local")
    quick = importlib.import_module(module.__name__ + ".quick_local")
    before = (home / "config.yaml").read_bytes()
    calls = []

    def provision(self, *, hermes_home, preflight):
        assert hermes_home == home and preflight.paths.root.parent == home
        calls.append(self.allow_source_build)
        if not self.allow_source_build:
            raise quick.SourceBuildRequired("Build from source?")
        raise quick.QuickLocalSetupError("Extraction validation failed")

    monkeypatch.setattr(quick.QuickLocalSetup, "provision", provision)
    with scope(home):
        payload = {"values": {"setup_type": "quick_local", "usage_profile": "personal"}}
        with pytest.raises(MemoryProviderConfigConflictError) as error:
            provider.handle_desktop_config_action("save", payload, hermes_home=str(home))
        assert error.value.confirmation == "source_build"
        with pytest.raises(ValueError, match="Extraction validation failed"):
            provider.handle_desktop_config_action(
                "save", {**payload, "confirmations": {"source_build": True}}, hermes_home=str(home)
            )
    assert calls == [False, True]
    assert (home / "config.yaml").read_bytes() == before
