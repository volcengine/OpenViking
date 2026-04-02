"""Configuration module for vikingbot."""

from vikingbot.config.loader import load_config, get_config_path
from vikingbot.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
