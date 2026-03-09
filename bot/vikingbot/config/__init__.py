"""Configuration module for vikingbot."""

from vikingbot.config.loader import get_config_path, load_config
from vikingbot.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
