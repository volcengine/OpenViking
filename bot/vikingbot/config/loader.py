"""Configuration loading utilities."""

import json
import os
from pathlib import Path
from typing import Any
from loguru import logger
from vikingbot.config.schema import Config

CONFIG_PATH = None

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
        path = Path(env_path).expanduser()
        if path.exists():
            return path

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

            bot_server_data = bot_data.get("ov_server", {})
            ov_server_data = full_data.get("server", {})
            _merge_ov_server_config(bot_server_data, ov_server_data)
            bot_data["ov_server"] = bot_server_data

            return Config.model_validate(bot_data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def _merge_vlm_model_config(bot_data: dict, vlm_data: dict) -> None:
    """
    Merge vlm model config into bot config.

    Only sets model - provider config is read directly from OpenVikingConfig.
    """
    # Set default model from vlm.model
    if "agents" in bot_data:
        agents = bot_data["agents"]
        if "model" in agents and agents["model"]:
            return
    if vlm_data.get("model"):
        if "agents" not in bot_data:
            bot_data["agents"] = {}
        model = vlm_data["model"]
        provider = vlm_data.get("provider")
        bot_data["agents"]["model"] = model
        bot_data["agents"]["provider"] = provider if provider else ""
        bot_data["agents"]["api_base"] = vlm_data.get("api_base", "")
        bot_data["agents"]["api_key"] = vlm_data.get("api_key", "")
        if "extra_headers" in vlm_data and vlm_data["extra_headers"] is not None:
            bot_data["agents"]["extra_headers"] = vlm_data["extra_headers"]


def _merge_ov_server_config(bot_data: dict, ov_data: dict) -> None:
    """
    Merge ov_server config into bot config.
    """
    if "server_url" not in bot_data or not bot_data["server_url"]:
        host = ov_data.get("host", "127.0.0.1")
        port = ov_data.get("port", "1933")
        bot_data["server_url"] = f"http://{host}:{port}"
    if "root_api_key" not in bot_data or not bot_data["root_api_key"]:
        bot_data["root_api_key"] = ov_data.get("root_api_key", "")
    if "root_api_key" in bot_data and bot_data["root_api_key"]:
        bot_data["mode"] = "remote"
    else:
        bot_data["mode"] = "local"


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