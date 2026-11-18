"""Configuration schema, environment overrides and the standalone command line."""

import json
import sys

import pytest
from pydantic import ValidationError

from openviking.crypto import FileEncryptor
from openviking_gateway import cli, protocols
from openviking_gateway.app import create_app
from openviking_gateway.cli import load_config
from openviking_gateway.client import VikingError
from openviking_gateway.config import (
    PUBLIC_OPENVIKING_URLS,
    EmbeddedGatewayConfig,
    GatewayConfig,
    StandaloneGatewayConfig,
)
from openviking_gateway.standalone import build_encryptor


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    for field in {*EmbeddedGatewayConfig.model_fields, *StandaloneGatewayConfig.model_fields}:
        monkeypatch.delenv("OPENVIKING_GATEWAY_" + field.upper(), raising=False)
    monkeypatch.delenv("OPENVIKING_GATEWAY_CONFIG_FILE", raising=False)
    monkeypatch.setattr(
        cli, "DEFAULT_CONFIGS", (tmp_path / "home" / "gateway.conf", tmp_path / "etc.conf")
    )
    monkeypatch.setattr(cli, "USER_CONFIG", tmp_path / "home" / "gateway.conf")


def test_removed_fields_are_ignored_with_one_warning(caplog):
    legacy = {
        "enabled": True,
        "url": "http://127.0.0.1:1935",
        "port": 1935,
        "workers": 2,
        "encryption_key_env": "MY_KEY",
    }
    config = EmbeddedGatewayConfig.model_validate(legacy)
    assert config.enabled
    assert "Ignoring removed gateway settings encryption_key_env, port, url, workers" in caplog.text
    caplog.clear()
    StandaloneGatewayConfig(openviking_url="http://localhost:1933", encryption_key_env="MY_KEY")
    assert "Ignoring removed gateway settings encryption_key_env" in caplog.text
    with pytest.raises(ValidationError):
        EmbeddedGatewayConfig.model_validate({"enabled": True, "urll": "typo"})
    with pytest.raises(ValidationError):
        StandaloneGatewayConfig(openviking_url="http://localhost:1933", url="http://other")


def test_session_ttl_ignores_legacy_days_and_rejects_under_an_hour():
    assert GatewayConfig(session_ttl_days=7).session_ttl_seconds == 30 * 86400
    assert GatewayConfig(session_ttl_seconds=7200).session_ttl_seconds == 7200
    with pytest.raises(ValidationError):
        GatewayConfig(session_ttl_seconds=600)


def test_standalone_requires_own_url_and_rejects_server_config():
    with pytest.raises(ValidationError):
        StandaloneGatewayConfig()
    with pytest.raises(ValidationError):
        StandaloneGatewayConfig(openviking_url="http://localhost:1933", enabled=True)
    with pytest.raises(ValidationError):
        EmbeddedGatewayConfig(encryption={"enabled": True})


def test_standalone_encryption_block_uses_openviking_schema(tmp_path):
    with pytest.raises(ValidationError):
        StandaloneGatewayConfig(openviking_url="http://ov.test", encryption={"enabled": "maybe"})
    config = StandaloneGatewayConfig(openviking_url="http://ov.test", encryption={"enabled": False})
    assert build_encryptor(config) is None
    assert build_encryptor(StandaloneGatewayConfig(openviking_url="http://ov.test")) is None
    key_file = tmp_path / "master.key"
    config = StandaloneGatewayConfig(
        openviking_url="http://ov.test",
        encryption={"enabled": True, "provider": "local", "local": {"key_file": str(key_file)}},
    )
    assert "encryption" not in repr(config)
    assert isinstance(build_encryptor(config), FileEncryptor)


async def test_encryptor_bootstraps_inside_a_running_loop(tmp_path):
    # uvicorn calls the app factory from inside its event loop.
    config = StandaloneGatewayConfig(
        openviking_url="http://ov.test",
        encryption={"enabled": True, "local": {"key_file": str(tmp_path / "master.key")}},
    )
    assert isinstance(build_encryptor(config), FileEncryptor)


def test_management_key_expansion_and_length(monkeypatch):
    monkeypatch.setenv("MANAGEMENT_SECRET", "ovgm_" + "a" * 32)
    assert (
        EmbeddedGatewayConfig(management_key="$MANAGEMENT_SECRET").management_key
        == "ovgm_" + "a" * 32
    )
    with pytest.raises(ValidationError, match="at least 32"):
        GatewayConfig(management_key="short")
    with pytest.raises(ValidationError, match="at least 32"):
        GatewayConfig(management_key="$UNDEFINED_MANAGEMENT_SECRET_THAT_IS_LONG")


def test_embedded_environment_overrides_ov_conf(monkeypatch):
    monkeypatch.setenv("OPENVIKING_GATEWAY_MANAGEMENT_KEY", "e" * 32)
    monkeypatch.setenv("OPENVIKING_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("OPENVIKING_GATEWAY_STORAGE_PATH", "")
    config = EmbeddedGatewayConfig.model_validate({"storage_path": "/data/gateway"})
    assert config.enabled and config.management_key == "e" * 32
    assert config.storage_path == "/data/gateway"
    # A server whose ov.conf has no gateway section builds the default the same way.
    assert EmbeddedGatewayConfig().management_key == "e" * 32


def test_client_rules_validation_and_environment(monkeypatch):
    with pytest.raises(ValidationError, match="invalid regex"):
        GatewayConfig(client_rules={"auxiliary_patterns": {"add": ["Summarize (this"]}})
    with pytest.raises(ValidationError, match="tag names"):
        GatewayConfig(client_rules={"noise_tags": {"add": ["a>b"]}})
    with pytest.raises(ValidationError):
        GatewayConfig(client_rules={"auxiliary_patterns": ["replaces the list"]})
    rules = {"auxiliary_patterns": {"add": ["You are the Acme title writer"]}}
    monkeypatch.setenv("OPENVIKING_GATEWAY_CLIENT_RULES", json.dumps(rules))
    config = EmbeddedGatewayConfig.model_validate({"enabled": True})
    assert config.client_rules.auxiliary_patterns.add == ["You are the Acme title writer"]


def test_app_applies_client_rules_and_warns_about_stale_removals(tmp_path, caplog):
    config = GatewayConfig(
        storage_path=str(tmp_path),
        client_rules={
            "auxiliary_patterns": {"add": ["Acme title writer"], "remove": ["Old packaged rule"]}
        },
    )
    try:
        create_app(config, openviking_url="http://ov.test")
        assert "Acme title writer" in protocols.AUXILIARY_PATTERNS
        assert "auxiliary_patterns: Old packaged rule" in caplog.text
    finally:
        protocols.configure_rules()


def test_standalone_config_file_and_env_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(tmp_path / "ignored-ov.conf"))
    (tmp_path / "ignored-ov.conf").write_text('{"gateway":{"enabled":true}}')
    config_file = tmp_path / "gateway.conf"
    config_file.write_text(
        json.dumps(
            {
                "openviking_url": "https://example.test/ov/",
                "port": 9001,
                "storage_path": "$GATEWAY_DATA",
            }
        )
    )
    monkeypatch.setenv("GATEWAY_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("OPENVIKING_GATEWAY_PORT", "9002")
    monkeypatch.setenv("OPENVIKING_GATEWAY_MANAGEMENT_KEY", "m" * 32)
    config = load_config()
    assert config.openviking_url == "https://example.test/ov"
    assert config.port == 9002
    assert config.management_key == "m" * 32
    assert config.storage_path == str(tmp_path / "data")


def test_empty_environment_values_keep_file_values(tmp_path, monkeypatch):
    for field in StandaloneGatewayConfig.model_fields:
        monkeypatch.setenv("OPENVIKING_GATEWAY_" + field.upper(), "")
    config_file = tmp_path / "gateway.conf"
    config_file.write_text(
        json.dumps({"openviking_url": "https://example.test", "management_key": "f" * 32})
    )
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(config_file))
    config = load_config()
    assert config.openviking_url == "https://example.test"
    assert config.management_key == "f" * 32


def test_kubernetes_service_link_variables_are_ignored(monkeypatch):
    # A Service named openviking-gateway injects this into every pod in its namespace.
    monkeypatch.setenv("OPENVIKING_GATEWAY_PORT", "tcp://10.96.0.12:1935")
    config = StandaloneGatewayConfig.model_validate({"openviking_url": "http://openviking:1933"})
    assert config.port == StandaloneGatewayConfig.model_fields["port"].default


def test_config_lookup_order(tmp_path, monkeypatch):
    home, etc = cli.DEFAULT_CONFIGS
    assert cli.config_path() is None
    etc.write_text(json.dumps({"openviking_url": "http://etc.test"}))
    assert load_config().openviking_url == "http://etc.test"
    home.parent.mkdir()
    home.write_text(json.dumps({"openviking_url": "http://home.test"}))
    assert load_config().openviking_url == "http://home.test"
    explicit = tmp_path / "explicit.conf"
    explicit.write_text(json.dumps({"openviking_url": "http://explicit.test"}))
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(explicit))
    assert load_config().openviking_url == "http://explicit.test"
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(tmp_path / "missing.conf"))
    with pytest.raises(SystemExit, match="not found"):
        load_config()


def test_environment_alone_configures_a_container(monkeypatch):
    with pytest.raises(SystemExit, match="openviking-gateway init"):
        load_config()
    monkeypatch.setenv("OPENVIKING_GATEWAY_OPENVIKING_URL", "http://openviking:1933")
    monkeypatch.setenv("OPENVIKING_GATEWAY_HOST", "0.0.0.0")
    config = load_config()
    assert (config.openviking_url, config.host) == ("http://openviking:1933", "0.0.0.0")


@pytest.mark.parametrize("argv", [["--config", "{}", "init"], ["init", "--config", "{}"]])
def test_init_accepts_config_before_or_after_the_command(tmp_path, monkeypatch, argv):
    target = tmp_path / "custom.conf"
    calls = []
    monkeypatch.setattr(cli, "init", lambda path: calls.append(path) or 0)
    argv = [str(target) if a == "{}" else a for a in argv]
    monkeypatch.setattr(sys, "argv", ["openviking-gateway", *argv])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 0 and calls == [target]


def answer(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))


def test_init_writes_and_rewrites_gateway_conf(monkeypatch):
    path = cli.USER_CONFIG
    probes = []

    async def probe(url):
        probes.append(url)
        if len(probes) == 1:
            raise VikingError("openviking_unreachable")
        return {"status": "ok", "auth_mode": "api_key", "version": "0.5.1"}

    monkeypatch.setattr(cli, "probe", probe)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # Bad URL, unreachable URL (retry), reachable URL, host, default port, management key.
    answer(monkeypatch, "ftp://ov", "http://down.test", "y", "https://OV.test/", "0.0.0.0", "", "y")
    assert cli.init(path) == 0
    saved = json.loads(path.read_text())
    assert path.stat().st_mode & 0o777 == 0o600
    assert probes == ["http://down.test", "https://ov.test"]
    assert {k: saved[k] for k in ("openviking_url", "host", "port")} == {
        "openviking_url": "https://ov.test",
        "host": "0.0.0.0",
        "port": 1935,
    }
    key = saved["management_key"]
    assert key.startswith("ovgm_") and len(key) >= 37
    assert StandaloneGatewayConfig.model_validate(saved).management_key == key

    # A re-run keeps every value and secret on Enter, and fields init does not ask about.
    path.write_text(json.dumps({**saved, "storage_path": "/data/gateway"}))
    answer(monkeypatch, "", "", "", "", "")
    assert cli.init(path) == 0
    assert json.loads(path.read_text()) == {**saved, "storage_path": "/data/gateway"}

    # Declining the management key removes it.
    answer(monkeypatch, "", "", "", "n")
    assert cli.init(path) == 0
    assert "management_key" not in json.loads(path.read_text())


def test_init_keeps_env_reference_and_stops_without_terminal(monkeypatch):
    path = cli.USER_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"openviking_url": "$OV_URL"}))
    monkeypatch.setattr(cli, "probe", lambda url: pytest.fail("a $VAR URL is not probed"))
    answer(monkeypatch, "", "", "", "n")
    assert cli.init(path) == 0
    assert json.loads(path.read_text())["openviking_url"] == "$OV_URL"

    # Without a terminal every prompt returns its default; an invalid one must not loop.
    path.write_text(json.dumps({"openviking_url": "ftp://ov"}))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    answer(monkeypatch, "")
    assert cli.init(path) == 1


def test_main_loads_root_key_before_workers_start(monkeypatch):
    loaded = []

    class Provider:
        async def get_root_key(self):
            loaded.append(True)
            return b"k"

    class Encryptor:
        provider = Provider()

    async def check(config):
        pass

    monkeypatch.setattr(sys, "argv", ["openviking-gateway"])
    monkeypatch.setenv("OPENVIKING_GATEWAY_OPENVIKING_URL", "http://openviking:1933")
    monkeypatch.setattr(cli, "check_openviking", check)
    monkeypatch.setattr(cli, "build_encryptor", lambda config: Encryptor())
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: loaded.append("run"))
    cli.main()
    assert loaded == [True, "run"]


def test_public_url_normalization():
    config = StandaloneGatewayConfig(
        openviking_url="https://API.VIKINGDB.CN-BEIJING.VOLCES.COM/openviking/"
    )
    assert config.openviking_url in PUBLIC_OPENVIKING_URLS
    ipv6 = StandaloneGatewayConfig(openviking_url="http://[::1]:1933/")
    assert ipv6.openviking_url == "http://[::1]:1933"
    with pytest.raises(ValidationError):
        StandaloneGatewayConfig(openviking_url="http://localhost:99999")


def test_mcp_file_links_default_environment_and_validation(monkeypatch):
    assert GatewayConfig().mcp_file_links == "proxy"
    assert EmbeddedGatewayConfig(mcp_file_links="direct").mcp_file_links == "direct"
    monkeypatch.setenv("OPENVIKING_GATEWAY_MCP_FILE_LINKS", "direct")
    config = StandaloneGatewayConfig(openviking_url="http://openviking:1933")
    assert config.mcp_file_links == "direct"
    monkeypatch.setenv("OPENVIKING_GATEWAY_MCP_FILE_LINKS", "public")
    with pytest.raises(ValidationError):
        GatewayConfig()
    monkeypatch.delenv("OPENVIKING_GATEWAY_MCP_FILE_LINKS")
    with pytest.raises(ValidationError):
        GatewayConfig(mcp_file_links="public")
