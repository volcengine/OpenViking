"""Configuration loading utilities."""

import ipaddress
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger

from openviking.server.config import (
    ServerConfig,
    get_server_url_from_server_data,
)
from vikingbot.config.schema import Config

CONFIG_PATH = None
OPENVIKING_AUTH_CHECK_TIMEOUT_SECONDS = 2.0
VIKINGBOT_WITH_OPENVIKING_SERVER_ENV = "VIKINGBOT_WITH_OPENVIKING_SERVER"
VIKINGBOT_MANAGED_OV_SERVER_URL_ENV = "VIKINGBOT_MANAGED_OV_SERVER_URL"


def get_config_path() -> Path:
    """Get the path to ov.conf config file.

    Resolution order:
      1. OPENVIKING_CONFIG_FILE environment variable
      2. ~/.openviking/ov.conf
    """
    return _resolve_ov_conf_path()


def _resolve_ov_conf_path() -> Path:
    """Resolve the ov.conf file path."""
    # Check environment variable first
    env_path = os.environ.get("OPENVIKING_CONFIG_FILE")
    if env_path:
        return Path(env_path).expanduser()

    # Default path
    return Path.home() / ".openviking" / "ov.conf"


def get_data_dir() -> Path:
    """Get the vikingbot data directory."""
    from vikingbot.utils.helpers import get_data_path

    return get_data_path()


def ensure_config(config_path: Path | None = None) -> Config:
    """Ensure ov.conf exists, create with default bot config if not."""
    config_path = config_path or get_config_path()
    global CONFIG_PATH
    CONFIG_PATH = config_path

    if not config_path.exists():
        logger.info("Config not found, creating default config...")

        # Create directory if needed
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Create default config with empty bot section
        default_config = Config()
        save_config(default_config, config_path, include_defaults=True)
        logger.info(f"[green]✓[/green] Created default config at {config_path}")

    config = load_config()
    return config


def load_config() -> Config:
    """
    Load configuration from ov.conf's bot field, and merge vlm config for model.

    Args:
        config_path: Optional path to ov.conf file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = CONFIG_PATH or get_config_path()

    if path.exists():
        try:
            with open(path) as f:
                raw = f.read()

            # Expand $VAR and ${VAR} inside the JSON text (useful for container deployments).
            # Unset variables are left unchanged by expandvars().
            raw = os.path.expandvars(raw)

            full_data = json.loads(raw)

            # Extract bot section
            bot_data = full_data.get("bot", {})
            bot_data = convert_keys(bot_data)

            # Extract storage.workspace from root level, default to ~/.openviking_data
            storage_data = full_data.get("storage", {})
            if isinstance(storage_data, dict) and "workspace" in storage_data:
                bot_data["storage_workspace"] = storage_data["workspace"]
            else:
                bot_data["storage_workspace"] = "~/.openviking/data"

            # Extract and merge vlm config for model settings only
            # Provider config is directly read from OpenVikingConfig at runtime
            vlm_data = full_data.get("vlm", {})
            vlm_data = convert_keys(vlm_data)
            if vlm_data:
                _merge_vlm_model_config(bot_data, vlm_data)

            server_managed = _is_truthy_env(VIKINGBOT_WITH_OPENVIKING_SERVER_ENV)
            bot_server_data = {} if server_managed else bot_data.get("ov_server", {})
            server_section_present = "server" in full_data and isinstance(
                full_data.get("server"), dict
            )
            ov_server_data = full_data.get("server", {}) if server_section_present else {}
            effective_auth_mode, ov_server_source, api_key_source = _merge_ov_server_config(
                bot_server_data,
                ov_server_data,
                server_section_present=server_section_present,
            )
            managed_server_url = str(
                os.environ.get(VIKINGBOT_MANAGED_OV_SERVER_URL_ENV) or ""
            ).strip()
            if server_managed and managed_server_url:
                bot_server_data["server_url"] = managed_server_url.rstrip("/")
            bot_data["ov_server"] = bot_server_data

            config = Config.model_validate(bot_data)
            config.ov_server.set_effective_auth_mode(effective_auth_mode)
            config.ov_server.set_config_source(ov_server_source)
            config.ov_server.set_api_key_source(api_key_source)
            config.ov_server.set_server_managed(server_managed)

            return config
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def _merge_vlm_model_config(bot_data: dict, vlm_data: dict) -> None:
    """
    Merge vlm model config into bot config.

    Only sets model parameters - provider config is read directly from OpenVikingConfig.
    """
    if vlm_data:
        if "agents" not in bot_data:
            bot_data["agents"] = {}

    agents = bot_data.get("agents", {})
    if vlm_data and "timeout" not in agents:
        agents["timeout"] = vlm_data["timeout"] if "timeout" in vlm_data else 60.0

    # Set default model from vlm.model
    if "agents" in bot_data:
        if "model" in agents and agents["model"]:
            return
    if vlm_data.get("model"):
        model = vlm_data["model"]
        provider = vlm_data.get("provider")
        agents["model"] = model
        agents["provider"] = provider if provider else ""
        agents["api_base"] = vlm_data.get("api_base", "")
        agents["api_key"] = vlm_data.get("api_key", "")
        if "temperature" in vlm_data and "temperature" not in agents:
            agents["temperature"] = vlm_data["temperature"]
        if "extra_headers" in vlm_data and vlm_data["extra_headers"] is not None:
            agents["extra_headers"] = vlm_data["extra_headers"]


def _merge_ov_server_config(
    bot_data: dict,
    ov_data: dict,
    *,
    server_section_present: bool,
) -> tuple[str, str, str]:
    """
    Merge ov_server config into bot config.
    """
    server_data = ov_data if isinstance(ov_data, dict) else {}
    configured_server_url = str(bot_data.get("server_url") or "").strip()

    if configured_server_url:
        return _merge_external_ov_server_config(bot_data, configured_server_url)
    if not server_section_present:
        return _merge_no_ov_server_config(bot_data)

    return _merge_current_ov_server_config(bot_data, server_data)


def _merge_external_ov_server_config(bot_data: dict, server_url: str) -> tuple[str, str, str]:
    bot_data["server_url"] = server_url
    bot_data["mode"] = "remote"
    bot_data["api_key_type"] = _normalize_api_key_type(bot_data.get("api_key_type")) or "user"
    api_key_source = (
        "bot.ov_server.api_key" if str(bot_data.get("api_key") or "").strip() else "none"
    )
    return (
        _bot_auth_mode_from_api_key_type(bot_data["api_key_type"], "api_key"),
        "explicit",
        api_key_source,
    )


def _merge_no_ov_server_config(bot_data: dict) -> tuple[str, str, str]:
    bot_data["server_url"] = str(bot_data.get("server_url") or "").strip()
    bot_data["mode"] = "local"
    bot_data["api_key_type"] = _normalize_api_key_type(bot_data.get("api_key_type")) or "user"
    api_key_source = (
        "bot.ov_server.api_key" if str(bot_data.get("api_key") or "").strip() else "none"
    )
    return "", "none", api_key_source


def _merge_current_ov_server_config(
    bot_data: dict,
    server_data: dict,
) -> tuple[str, str, str]:
    bot_data["server_url"] = get_server_url_from_server_data(server_data)

    server_auth_mode = ServerConfig(
        auth_mode=server_data.get("auth_mode"),
        root_api_key=server_data.get("root_api_key"),
    ).get_effective_auth_mode()
    api_key_type = _normalize_api_key_type(bot_data.get("api_key_type")) or (
        "root" if server_auth_mode == "trusted" else "user"
    )
    bot_data["api_key_type"] = api_key_type

    api_key_source = (
        "bot.ov_server.api_key" if str(bot_data.get("api_key") or "").strip() else "none"
    )
    server_root_api_key = str(server_data.get("root_api_key") or "").strip()
    if api_key_type == "root" and server_auth_mode == "trusted" and server_root_api_key:
        bot_data["api_key"] = server_root_api_key
        api_key_source = "server.root_api_key"

    effective_auth_mode = _bot_auth_mode_from_api_key_type(api_key_type, server_auth_mode)
    mode = "local" if effective_auth_mode == "dev" else "remote"
    bot_data["mode"] = mode
    return effective_auth_mode, "inherited", api_key_source


def _is_truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_api_key_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"root", "user"} else ""


def _bot_auth_mode_from_api_key_type(api_key_type: str, server_auth_mode: str) -> str:
    if api_key_type == "root":
        return "trusted"
    if server_auth_mode == "dev":
        return "dev"
    return "api_key"


def _ov_server_auth_mode(ov_server: Any) -> str:
    effective_auth_mode = str(getattr(ov_server, "effective_auth_mode", "") or "").strip().lower()
    if effective_auth_mode in {"trusted", "api_key", "dev"}:
        return effective_auth_mode

    api_key_type = _normalize_api_key_type(getattr(ov_server, "api_key_type", "user"))
    if api_key_type == "root":
        return "trusted"
    return "api_key"


def _ov_server_config_source(ov_server: Any) -> str:
    getter = getattr(ov_server, "get_config_source", None)
    if callable(getter):
        source = getter()
    else:
        source = getattr(ov_server, "_source", "none")
    source = str(source or "none").strip().lower()
    return source if source in {"explicit", "inherited", "none"} else "none"


def _ov_server_api_key_source(ov_server: Any) -> str:
    getter = getattr(ov_server, "get_api_key_source", None)
    if callable(getter):
        source = getter()
    else:
        source = getattr(ov_server, "_api_key_source", "none")
    source = str(source or "none").strip().lower()
    allowed = {"bot.ov_server.api_key", "server.root_api_key", "none"}
    return source if source in allowed else "none"


def _ov_server_is_server_managed(ov_server: Any) -> bool:
    getter = getattr(ov_server, "is_server_managed", None)
    if callable(getter):
        return bool(getter())
    return bool(getattr(ov_server, "_server_managed", False))


def _ov_server_trusted_api_key(ov_server: Any) -> str:
    if _normalize_api_key_type(getattr(ov_server, "api_key_type", "")) == "root":
        return str(getattr(ov_server, "api_key", "") or "").strip()
    return ""


@dataclass
class _OpenVikingHTTPResult:
    ok: bool
    status_code: int | None = None
    data: dict[str, Any] | None = None
    error: str = ""


def _openviking_url(server_url: str, path: str) -> str:
    return f"{str(server_url or '').rstrip('/')}/{path.lstrip('/')}"


def _request_openviking_json(
    server_url: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> _OpenVikingHTTPResult:
    try:
        with httpx.Client(
            timeout=OPENVIKING_AUTH_CHECK_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            response = client.get(_openviking_url(server_url, path), headers=headers)
    except httpx.HTTPError as exc:
        return _OpenVikingHTTPResult(ok=False, error=exc.__class__.__name__)

    data: dict[str, Any] | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            data = parsed
    except ValueError:
        data = None
    return _OpenVikingHTTPResult(
        ok=200 <= response.status_code < 300,
        status_code=response.status_code,
        data=data,
    )


def _result_reason(result: _OpenVikingHTTPResult) -> str:
    if result.error:
        return result.error
    if result.status_code is not None:
        return f"HTTP {result.status_code}"
    return "unknown error"


def _server_unavailable_warning(server_url: str, result: _OpenVikingHTTPResult) -> None:
    print(
        f"Warning: OpenViking server at {server_url} is unavailable "
        f"({_result_reason(result)}). VikingBot will start in standalone mode; "
        "OpenViking memory and file tools are disabled.",
        file=sys.stderr,
    )


def _raise_server_unavailable(server_url: str, result: _OpenVikingHTTPResult) -> None:
    print(
        "Error: configured bot.ov_server.server_url is unavailable.\n"
        f"OpenViking server URL: {server_url}\n"
        f"Reason: {_result_reason(result)}\n"
        "Start the configured OpenViking server or update bot.ov_server.server_url.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _raise_server_unhealthy(server_url: str, result: _OpenVikingHTTPResult) -> None:
    print(
        "Error: OpenViking server is reachable but not usable by VikingBot.\n"
        f"OpenViking server URL: {server_url}\n"
        f"Reason: {_result_reason(result)}\n"
        "Check the OpenViking server status and authentication configuration.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _inherited_auth_mode_change_hint(actual_auth_mode: str, current_auth_mode: str) -> str:
    hint = (
        "VikingBot inherits the OpenViking auth mode from the current ov.conf server section. "
        "The running OpenViking process was started with different authentication settings.\n"
        f"Fix: restart OpenViking server with the current ov.conf to apply auth_mode="
        f"'{current_auth_mode}', or update ov.conf server.auth_mode/root_api_key so it "
        f"resolves to '{actual_auth_mode}', then start the gateway again."
    )
    if actual_auth_mode == "api_key":
        hint += (
            " If api_key mode is intended, also configure bot.ov_server.api_key with an "
            "OpenViking User/Admin API key."
        )
    elif actual_auth_mode == "trusted":
        hint += (
            " If trusted mode is intended, configure server.auth_mode='trusted' and "
            "server.root_api_key in the same ov.conf."
        )
    if current_auth_mode == "dev":
        hint += " A dev-mode VikingBot gateway must listen on localhost."
    return hint


def _auth_mode_change_hint(
    actual_auth_mode: str,
    current_auth_mode: str,
    source: str,
) -> str:
    if source == "inherited":
        return _inherited_auth_mode_change_hint(actual_auth_mode, current_auth_mode)
    if actual_auth_mode == "trusted":
        return (
            "To use this server, set bot.ov_server.api_key_type to 'root' and configure "
            "bot.ov_server.api_key with the OpenViking root API key, or remove the "
            "bot.ov_server override so VikingBot "
            "inherits server.auth_mode='trusted' from the same ov.conf."
        )
    if actual_auth_mode == "api_key":
        return (
            "To use this server, set bot.ov_server.api_key_type to 'user' and configure "
            "bot.ov_server.api_key with an OpenViking User/Admin API key, or change the "
            "OpenViking server.auth_mode and restart the server."
        )
    if actual_auth_mode == "dev":
        return (
            "To use this server, let VikingBot inherit the same dev OpenViking server "
            "configuration, or change the OpenViking server.auth_mode and restart the server."
        )
    return (
        "Update bot.ov_server.api_key_type or the OpenViking server.auth_mode so both "
        f"sides use the same auth mode. VikingBot currently expects '{current_auth_mode}'."
    )


def _raise_auth_mode_mismatch(
    server_url: str,
    actual_auth_mode: str,
    current_auth_mode: str,
    source: str,
) -> None:
    if source == "inherited":
        print(
            "Error: running OpenViking auth mode does not match the current ov.conf.\n"
            f"OpenViking server URL: {server_url}\n"
            f"Running server auth_mode: {actual_auth_mode}\n"
            f"Current ov.conf server auth_mode: {current_auth_mode}\n"
            f"{_auth_mode_change_hint(actual_auth_mode, current_auth_mode, source)}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(
        "Error: OpenViking auth mode mismatch.\n"
        f"OpenViking server URL: {server_url}\n"
        f"Actual server auth_mode: {actual_auth_mode}\n"
        f"VikingBot current auth_mode: {current_auth_mode}\n"
        f"{_auth_mode_change_hint(actual_auth_mode, current_auth_mode, source)}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _mark_standalone_mode(ov_server: Any) -> None:
    ov_server.server_url = ""
    ov_server.mode = "local"
    setter = getattr(ov_server, "set_effective_auth_mode", None)
    if callable(setter):
        setter("")
    else:
        ov_server._effective_auth_mode = ""
    source_setter = getattr(ov_server, "set_config_source", None)
    if callable(source_setter):
        source_setter("none")
    else:
        ov_server._source = "none"
    key_source_setter = getattr(ov_server, "set_api_key_source", None)
    if callable(key_source_setter):
        key_source_setter("none")
    else:
        ov_server._api_key_source = "none"


def _set_runtime_auth_mode(ov_server: Any, auth_mode: str) -> None:
    auth_mode = str(auth_mode or "").strip().lower()
    setter = getattr(ov_server, "set_effective_auth_mode", None)
    if callable(setter):
        setter(auth_mode)
    else:
        ov_server._effective_auth_mode = auth_mode
    if auth_mode == "trusted":
        ov_server.api_key_type = "root"
    elif auth_mode == "api_key":
        ov_server.api_key_type = "user"
    if auth_mode == "dev":
        ov_server.mode = "local"
    elif auth_mode:
        ov_server.mode = "remote"


def _is_loopback_host(host: str) -> bool:
    host = str(host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return _is_loopback_host(host)


def _validate_dev_boundary(config: Config, server_url: str) -> None:
    gateway = getattr(config, "gateway", None)
    gateway_host = str(getattr(gateway, "host", "127.0.0.1") or "127.0.0.1").strip()
    if _is_loopback_host(gateway_host) and _is_loopback_url(server_url):
        return
    print(
        "Error: OpenViking dev auth can only be used when gateway and OpenViking server "
        "are localhost.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _raise_api_key_mode_requires_user_key(ov_server: Any) -> None:
    key_source = _ov_server_api_key_source(ov_server)
    key_hint = "bot.ov_server.api_key"
    print(
        "Error: OpenViking is configured for api_key mode, but VikingBot does not have "
        "a valid OpenViking User/Admin API key.\n"
        f"API key source: {key_source}\n"
        f"Fix: configure {key_hint} with a User/Admin API key. "
        "Root API keys cannot access OpenViking data APIs in api_key mode.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _raise_api_key_mode_root_key() -> None:
    print(
        "Error: bot.ov_server.api_key resolves to a ROOT API key, but OpenViking "
        "api_key mode requires a User/Admin API key for memory and file data APIs. "
        "Configure bot.ov_server.api_key with a User/Admin API key.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _raise_trusted_mode_requires_root_key(ov_server: Any) -> None:
    key_source = _ov_server_api_key_source(ov_server)
    print(
        "Error: VikingBot is configured for trusted OpenViking access, but no valid "
        "root API key is available.\n"
        f"API key source: {key_source}\n"
        "Fix: configure bot.ov_server.api_key with the OpenViking root API key for an "
        "explicit bot.ov_server, or configure server.root_api_key when inheriting the "
        "same ov.conf.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _validate_api_key_mode_key(ov_server: Any, server_url: str) -> None:
    api_key = str(getattr(ov_server, "api_key", "") or "").strip()
    api_key_type = _normalize_api_key_type(getattr(ov_server, "api_key_type", "user")) or "user"
    if _ov_server_is_server_managed(ov_server):
        return
    if not api_key or api_key_type != "user":
        _raise_api_key_mode_requires_user_key(ov_server)

    result = _request_openviking_json(
        server_url,
        "/health",
        headers={"X-API-Key": api_key},
    )
    if not result.ok:
        _raise_api_key_mode_requires_user_key(ov_server)

    data = result.data or {}
    role = str(data.get("role") or "").strip().lower()
    account_id = str(data.get("account_id") or "").strip()
    user_id = str(data.get("user_id") or "").strip()
    if role in {"user", "admin"} and account_id and user_id:
        return
    if role == "root":
        _raise_api_key_mode_root_key()
    _raise_api_key_mode_requires_user_key(ov_server)


def _validate_trusted_mode_key(ov_server: Any, server_url: str) -> None:
    root_api_key = _ov_server_trusted_api_key(ov_server)
    if not root_api_key:
        _raise_trusted_mode_requires_root_key(ov_server)
    account_id = str(getattr(ov_server, "account_id", "") or "default").strip()
    user_id = str(getattr(ov_server, "admin_user_id", "") or "default").strip()
    headers = {
        "X-OpenViking-Account": account_id,
        "X-OpenViking-User": user_id,
    }
    if root_api_key:
        headers["X-API-Key"] = root_api_key

    result = _request_openviking_json(server_url, "/api/v1/system/status", headers=headers)
    if result.ok:
        return

    if result.status_code in {401, 403}:
        _raise_trusted_mode_requires_root_key(ov_server)

    print(
        f"Error: VikingBot could not validate trusted OpenViking access at {server_url} "
        f"({_result_reason(result)}).",
        file=sys.stderr,
    )
    raise SystemExit(1)


def validate_openviking_auth(config: Config) -> None:
    """Validate VikingBot's OpenViking server, auth mode, and API key wiring."""
    ov_server = config.ov_server
    server_url = str(getattr(ov_server, "server_url", "") or "").strip()
    source = _ov_server_config_source(ov_server)
    if not server_url:
        print(
            "Warning: no available OpenViking server is configured. VikingBot will run "
            "in standalone mode; OpenViking memory and file tools are disabled.",
            file=sys.stderr,
        )
        return

    auth_mode = _ov_server_auth_mode(ov_server)

    # Build headers with API key if configured
    # Public gateways like Volcengine VikingDB require X-API-Key on every request
    headers: dict[str, str] = {}
    api_key = getattr(ov_server, "api_key", None)
    if api_key:
        headers["X-API-Key"] = api_key

    health = _request_openviking_json(server_url, "/health", headers=headers)
    if not health.ok:
        if _ov_server_is_server_managed(ov_server):
            print(
                f"Warning: managed OpenViking server at {server_url} is not ready yet "
                f"({_result_reason(health)}). VikingBot will keep the inherited upstream "
                "and wait for openviking-server to start.",
                file=sys.stderr,
            )
            return
        if health.status_code in {401, 403}:
            if auth_mode == "trusted":
                _raise_trusted_mode_requires_root_key(ov_server)
            if auth_mode == "api_key":
                _raise_api_key_mode_requires_user_key(ov_server)
            _raise_server_unhealthy(server_url, health)
        if health.status_code is not None and not health.error:
            _raise_server_unhealthy(server_url, health)
        if source == "explicit":
            _raise_server_unavailable(server_url, health)
        _server_unavailable_warning(server_url, health)
        _mark_standalone_mode(ov_server)
        return

    health_data = health.data or {}
    actual_auth_mode = str(health_data.get("auth_mode") or "").strip().lower()
    if not actual_auth_mode:
        _raise_server_unhealthy(
            server_url,
            _OpenVikingHTTPResult(
                ok=False, status_code=health.status_code, error="missing auth_mode"
            ),
        )
    if actual_auth_mode != auth_mode:
        if source == "explicit":
            _set_runtime_auth_mode(ov_server, actual_auth_mode)
            auth_mode = actual_auth_mode
        else:
            _raise_auth_mode_mismatch(server_url, actual_auth_mode, auth_mode, source)

    if auth_mode == "trusted":
        _validate_trusted_mode_key(ov_server, server_url)
        return
    if auth_mode == "api_key":
        _validate_api_key_mode_key(ov_server, server_url)
        return
    if auth_mode == "dev":
        _validate_dev_boundary(config, server_url)
        return
    return


def save_config(
    config: Config, config_path: Path | None = None, include_defaults: bool = False
) -> None:
    """
    Save configuration to ov.conf's bot field, preserving other sections.

    Args:
        config: Configuration to save.
        config_path: Optional path to ov.conf file. Uses default if not provided.
        include_defaults: Whether to include default values in the saved config.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing config if it exists
    full_data = {}
    if path.exists():
        try:
            with open(path) as f:
                full_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # Update bot section - only save fields that were explicitly set
    bot_data = config.model_dump(exclude_unset=not include_defaults)
    if bot_data:
        full_data["bot"] = convert_to_camel(bot_data)
    else:
        full_data.pop("bot", None)

    # Write back full config
    with open(path, "w") as f:
        json.dump(full_data, f, indent=2)


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        return {camel_to_snake(k): convert_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        return {snake_to_camel(k): convert_to_camel(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_to_camel(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])
