"""Exercise the external setup wizard and its saved Hermes session policy."""

import copy
import json
import os
from types import SimpleNamespace

import pytest
from hermes_cli.config import yaml


def patch_menu(monkeypatch, select):
    from hermes_cli import curses_ui

    def radio(title, options, *, selected=0, **kwargs):
        return select(title, options, default=selected, **kwargs)

    monkeypatch.setattr(curses_ui, "curses_radiolist", radio)


def setup_state(external_provider, monkeypatch, *, route="local", line_ending=b"\n"):
    import hermes_cli.memory_setup as setup

    home, provider, module, _ = external_provider("setup-profile")
    config = {
        "group_sessions_per_user": True,
        "thread_sessions_per_user": False,
        "memory": {"provider": "openviking", "openviking": {"recall_limit": 9}},
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    (home / ".env").write_bytes(
        b"UNRELATED=keep" + line_ending + b"OPENVIKING_RECALL_SCOPE=shared" + line_ending
    )
    monkeypatch.setenv("OPENVIKING_RECALL_SCOPE", "shared")
    values = {"endpoint": "http://127.0.0.1:1933", "user": "example"}
    saved = module._default_ovcli_config_path().with_name("ovcli.conf.existing")
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text('{"url":"http://127.0.0.1:1933","user":"example"}')
    profiles = (
        [module._OvcliProfile("saved", "existing", saved, values)] if route == "linked" else []
    )
    monkeypatch.setattr(module, "_discover_ovcli_profiles", lambda: profiles)
    monkeypatch.setattr(module, "_validate_openviking_reachability", lambda *_: (True, "ok"))
    monkeypatch.setattr(
        module, "_validate_openviking_setup_values", lambda *_, **__: (True, "ok", None)
    )
    monkeypatch.setattr(
        setup,
        "_prompt",
        lambda label, **_: "new-profile" if "profile name" in label else values["endpoint"],
    )
    return home, provider, module, config, setup


def select_profile(profile, route, menus, setup):
    def select(title, options, **kwargs):
        menus.append((title, options, kwargs))
        return {
            "  OpenViking usage profile": 0 if profile == "personal" else 1,
            "  Confirm Shared Agent": 0,
            "  OpenViking config source": 0,
            "  OpenViking profile": 0,
            "  OpenViking connection": 1,
            "  OpenViking credential": 2,
            "  Save OpenViking config": 1 if route == "mirror" else 0,
        }.get(title, setup._CANCELLED)

    return select


@pytest.mark.parametrize("route", ["local", "linked", "mirror"])
@pytest.mark.parametrize("profile", ["personal", "shared"])
@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_setup_persists_preset_and_real_gateway_session_boundaries(
    external_provider, monkeypatch, capsys, route, profile, line_ending
):
    from gateway.config import Platform
    from gateway.session import build_session_key
    from hermes_constants import (
        get_hermes_home,
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    home, provider, module, config, setup = setup_state(
        external_provider, monkeypatch, route=route, line_ending=line_ending
    )
    other_home, _, _, _ = external_provider("other-profile")
    other_before = (other_home / "config.yaml").read_bytes()
    menus = []
    patch_menu(monkeypatch, select_profile(profile, route, menus, setup))
    token = set_hermes_home_override(other_home)
    try:
        provider.post_setup(str(home), config)
        assert get_hermes_home() == other_home
    finally:
        reset_hermes_home_override(token)
    output = capsys.readouterr().out
    assert (
        "Personal recall enabled. Conversation-sharing settings are unchanged." in output
    ) == (profile == "personal")
    assert (
        "Restart the Hermes gateway to apply the shared session settings." in output
    ) == (profile == "shared")
    assert (other_home / "config.yaml").read_bytes() == other_before
    saved = yaml.safe_load((home / "config.yaml").read_text())
    settings = saved["memory"]["openviking"]
    assert settings["recall_scope"] == ("peer" if profile == "personal" else "shared")
    assert settings["recall_limit"] == 9
    assert settings["use_ovcli_config"] == (route != "local")
    assert saved["group_sessions_per_user"] == (profile == "personal")
    assert saved["thread_sessions_per_user"] is False
    assert "UNRELATED=keep" in (home / ".env").read_text()
    assert "OPENVIKING_RECALL_SCOPE=" not in (home / ".env").read_text()
    env_bytes = (home / ".env").read_bytes()
    assert env_bytes.startswith(b"UNRELATED=keep" + line_ending)
    assert env_bytes == line_ending.join(env_bytes.splitlines()) + line_ending
    assert (
        module.OpenVikingMemoryProvider._setting("recall_scope", settings)
        == settings["recall_scope"]
    )
    assert [item[0] for item in menus].count("  Confirm Shared Agent") == (profile == "shared")
    assert len(menus[0][1]) == 2
    if profile == "shared":
        confirmation = next(menu for menu in menus if menu[0] == "  Confirm Shared Agent")
        assert confirmation[2]["default"] == 1
        assert "across chats" in confirmation[2]["description"]
        assert "Different groups keep separate" in confirmation[2]["description"]

    def key(sender, *, chat="group-a", thread=None):
        source = SimpleNamespace(
            platform=Platform.TELEGRAM,
            chat_type="group",
            chat_id=chat,
            thread_id=thread,
            prospective_thread_id=None,
            user_id=sender,
            user_id_alt=None,
        )
        return build_session_key(
            source,
            group_sessions_per_user=saved["group_sessions_per_user"],
            thread_sessions_per_user=saved["thread_sessions_per_user"],
        )

    assert (key("alice") == key("bob")) == (profile == "shared")
    assert key("alice", thread="topic") == key("bob", thread="topic")
    assert key("alice", chat="group-a") != key("alice", chat="group-b")


@pytest.mark.parametrize("stage", ["usage", "confirm", "connection", "save", "validation"])
def test_cancelled_or_failed_setup_does_not_apply_preset(
    external_provider, monkeypatch, capsys, stage
):
    home, provider, module, config, setup = setup_state(
        external_provider, monkeypatch, route="linked" if stage == "validation" else "local"
    )
    before = copy.deepcopy(config)
    paths = [
        home / "config.yaml",
        home / ".env",
        module._default_ovcli_config_path().with_name("ovcli.conf.existing"),
    ]
    contents = {path: path.read_bytes() for path in paths}
    selectors = {
        "usage": [setup._CANCELLED],
        "confirm": [1, setup._CANCELLED],
        "connection": [1, 0, setup._CANCELLED],
        "save": [1, 0, 1, 2, setup._CANCELLED],
        "validation": [1, 0, 0, 0, setup._CANCELLED],
    }
    selections = iter(selectors[stage])
    patch_menu(monkeypatch, lambda *_, **__: next(selections))
    if stage == "validation":
        monkeypatch.setattr(
            module,
            "_validate_openviking_setup_values",
            lambda *_, **__: (False, "unavailable", None),
        )
    provider.post_setup(str(home), config)
    output = capsys.readouterr().out
    assert "Personal recall enabled." not in output
    assert "Restart the Hermes gateway" not in output
    assert config == before
    assert all(path.read_bytes() == content for path, content in contents.items())
    assert not (module._default_ovcli_config_path().parent / "ovcli.conf.new-profile").exists()


def test_saved_selection_and_back_from_shared_keep_personal_session_policy(
    external_provider, monkeypatch
):
    home, provider, _, config, setup = setup_state(external_provider, monkeypatch)
    config["memory"]["openviking"]["recall_scope"] = "shared"
    config["group_sessions_per_user"] = False
    config["thread_sessions_per_user"] = True
    selections = iter([1, 1, 0, 1, 2, 0])
    menus = []

    def select(title, options, **kwargs):
        menus.append((title, options, kwargs))
        return next(selections)

    patch_menu(monkeypatch, select)
    provider.post_setup(str(home), config)
    assert menus[0][2]["default"] == 1
    assert menus[1][2]["default"] == 1  # Sharing always needs explicit confirmation.
    assert len(menus[0][1]) == len(menus[2][1]) == 2
    saved = yaml.safe_load((home / "config.yaml").read_text())
    assert saved["memory"]["openviking"]["recall_scope"] == "peer"
    assert saved["group_sessions_per_user"] is False
    assert saved["thread_sessions_per_user"] is True


@pytest.mark.parametrize(
    "platform,sender,expected",
    [
        (
            "google/chat",
            "users/alice@example.com",
            "google-chat.users-alice-example.com-1daa15b42c91",
        ),
        (
            "google/chat",
            "users:alice@example.com",
            "google-chat.users-alice-example.com-eaebc6db16c3",
        ),
        ("telegram", "x" * 500, "telegram." + "x" * 106 + "-c022671d1ba8"),
        ("telegram", "alice@@example.com", "telegram.alice-example.com-c48af2cb6b43"),
    ],
)
def test_peer_paths_match_original_hermes_pr(external_provider, platform, sender, expected):
    _, _, module, _ = external_provider("peer-paths")
    assert module._gateway_peer_id(platform, sender) == expected


@pytest.mark.parametrize(
    "cloud, endpoint",
    [
        ("Volcengine", "https://api.vikingdb.cn-beijing.volces.com/openviking"),
        ("BytePlus", "https://api.vikingdb.ap-southeast-1.bytepluses.com/openviking"),
    ],
)
@pytest.mark.parametrize("route", ["hermes", "mirror"])
@pytest.mark.parametrize("profile", ["personal", "shared"])
def test_managed_provider_setup_saves_only_the_selected_connection(
    external_provider, monkeypatch, cloud, endpoint, route, profile
):
    home, provider, module, config, setup = setup_state(external_provider, monkeypatch)
    monkeypatch.setenv("OPENVIKING_LANG", "en")
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "https://old.example/openviking")
    monkeypatch.setenv("OPENVIKING_API_KEY", "old-key")
    prompts, menus, checks = [], [], []

    def prompt(label, **kwargs):
        prompts.append((label, kwargs))
        if "profile name" in label:
            return "managed-profile"
        assert "API key" in label
        assert kwargs["secret"] is True
        return " new-key "

    def select(title, options, **kwargs):
        menus.append((title, options, kwargs))
        return {
            "  OpenViking usage profile": int(profile == "shared"),
            "  Confirm Shared Agent": 0,
            "  OpenViking connection": 0,
            "  Cloud provider": int(cloud == "BytePlus"),
            "  Save OpenViking config": int(route == "mirror"),
        }.get(title, setup._CANCELLED)

    def validate(values, **kwargs):
        checks.append((dict(values), kwargs))
        return True, "", "user"

    monkeypatch.setattr(setup, "_prompt", prompt)
    monkeypatch.setattr(module, "_validate_openviking_setup_values", validate)
    def unexpected_probe(*_):
        pytest.fail("Managed presets must wait for the API key before probing")

    monkeypatch.setattr(module, "_validate_openviking_reachability", unexpected_probe)
    patch_menu(monkeypatch, select)
    provider.post_setup(str(home), config)

    assert len(checks) == 1
    assert checks[0][0]["endpoint"] == endpoint
    assert checks[0][0]["api_key"] == "new-key"
    assert checks[0][0]["root_api_key"] == ""
    assert checks[0][1]["require_api_key"] is True
    assert all("API key type" not in title for title, _, _ in menus)
    cloud_menu = next(menu for menu in menus if menu[0] == "  Cloud provider")
    assert [label.partition(" - ")[0] for label in cloud_menu[1]] == ["Volcengine", "BytePlus"]
    assert cloud_menu[2]["default"] == 1
    assert all("URL" not in label for label, _ in prompts)
    saved = yaml.safe_load((home / "config.yaml").read_text())
    settings = saved["memory"]["openviking"]
    assert settings["recall_scope"] == ("shared" if profile == "shared" else "peer")
    assert saved["group_sessions_per_user"] == (profile == "personal")
    if route == "mirror":
        path = module._default_ovcli_config_path().with_name("ovcli.conf.managed-profile")
        assert json.loads(path.read_text()) == {"url": endpoint, "api_key": "new-key"}
        assert settings["ovcli_config_path"] == str(path)
        assert "OPENVIKING_API_KEY=" not in (home / ".env").read_text()
        assert "OPENVIKING_API_KEY" not in os.environ
    else:
        env_text = (home / ".env").read_text()
        assert f"OPENVIKING_ENDPOINT={endpoint}" in env_text
        assert "OPENVIKING_API_KEY=new-key" in env_text
        assert os.environ["OPENVIKING_API_KEY"] == "new-key"
        assert not settings["use_ovcli_config"]
    assert "old-key" not in (home / ".env").read_text()


@pytest.mark.parametrize("cloud", ["Volcengine", "BytePlus"])
@pytest.mark.parametrize("stage", ["provider", "empty_key", "invalid_key", "save"])
def test_cancelled_managed_setup_preserves_profiles_and_credentials(
    external_provider, monkeypatch, cloud, stage
):
    home, provider, module, config, setup = setup_state(external_provider, monkeypatch)
    before = copy.deepcopy(config)
    paths = [home / "config.yaml", home / ".env"]
    contents = {path: path.read_bytes() for path in paths}
    monkeypatch.setenv("OPENVIKING_API_KEY", "old-key")
    prompts, checks = [], []
    def prompt(label, **kwargs):
        prompts.append(label)
        assert "API key" in label
        return "   " if stage == "empty_key" else "new-key"

    def select(title, options, **kwargs):
        return {
            "  OpenViking usage profile": 0,
            "  OpenViking connection": 0,
            "  Cloud provider": setup._CANCELLED if stage == "provider" else int(cloud == "BytePlus"),
        }.get(title, setup._CANCELLED)

    def validate(values, **kwargs):
        checks.append(dict(values))
        return (False, "invalid key", None) if stage == "invalid_key" else (True, "", "user")

    monkeypatch.setattr(setup, "_prompt", prompt)
    monkeypatch.setattr(module, "_validate_openviking_setup_values", validate)
    patch_menu(monkeypatch, select)
    provider.post_setup(str(home), config)
    assert config == before
    assert all(path.read_bytes() == contents[path] for path in paths)
    assert os.environ["OPENVIKING_API_KEY"] == "old-key"
    assert not list(module._default_ovcli_config_path().parent.glob("ovcli.conf.managed*"))
    if stage == "provider":
        assert not prompts
    if stage not in {"invalid_key", "save"}:
        assert checks == []


@pytest.mark.parametrize(
    "environment, expected_provider",
    [
        ({"OPENVIKING_LANG": "zh"}, 0),
        ({"OPENVIKING_LANG": "en", "LANG": "zh_CN.UTF-8"}, 1),
        ({"LC_ALL": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}, 0),
        ({"LC_MESSAGES": "zh_HK.UTF-8", "LANG": "en_US.UTF-8"}, 0),
        ({"LANG": "ja_JP.UTF-8"}, 1),
        ({}, 1),
    ],
)
def test_managed_provider_default_uses_the_user_language(
    external_provider, monkeypatch, environment, expected_provider
):
    home, provider, module, config, setup = setup_state(external_provider, monkeypatch)
    before = copy.deepcopy(config)
    for key in ("OPENVIKING_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    defaults, endpoints = [], []

    def select(title, options, **kwargs):
        if title == "  Cloud provider":
            defaults.append(kwargs["default"])
            return kwargs["default"]
        return {
            "  OpenViking usage profile": 0,
            "  OpenViking connection": 0,
        }.get(title, setup._CANCELLED)

    def validate(values, **kwargs):
        endpoints.append(values["endpoint"])
        return True, "", "user"

    monkeypatch.setattr(setup, "_prompt", lambda label, **kwargs: "new-key")
    monkeypatch.setattr(module, "_validate_openviking_setup_values", validate)
    patch_menu(monkeypatch, select)
    provider.post_setup(str(home), config)
    assert defaults == [expected_provider]
    assert endpoints == [
        module._OPENVIKING_SERVICE_ENDPOINT
        if expected_provider == 0 else module._BYTEPLUS_SERVICE_ENDPOINT
    ]
    assert config == before


@pytest.mark.parametrize("cloud", ["Volcengine", "BytePlus"])
def test_managed_key_retry_validates_only_the_replacement_key(
    external_provider, monkeypatch, cloud
):
    home, provider, module, config, setup = setup_state(external_provider, monkeypatch)
    keys = iter([" \t ", "replacement-key"])
    checks = []

    def prompt(label, **kwargs):
        assert "API key" in label
        return next(keys)

    def select(title, options, **kwargs):
        return int(title == "  Cloud provider" and cloud == "BytePlus")

    def validate(values, **kwargs):
        checks.append(dict(values))
        return True, "", "user"

    monkeypatch.setattr(setup, "_prompt", prompt)
    monkeypatch.setattr(module, "_validate_openviking_setup_values", validate)
    patch_menu(monkeypatch, select)
    provider.post_setup(str(home), config)
    assert len(checks) == 1
    assert checks[0]["endpoint"] == (
        module._BYTEPLUS_SERVICE_ENDPOINT if cloud == "BytePlus" else module._OPENVIKING_SERVICE_ENDPOINT
    )
    assert checks[0]["api_key"] == "replacement-key"
    assert os.environ["OPENVIKING_API_KEY"] == "replacement-key"
