# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Configuration file loading utilities.

Provides a three-level resolution chain for locating config files:
  1. Explicit path (constructor parameter / --config)
  2. Environment variable
  3. Default path (~/.openviking/)
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CONFIG_DIR = Path.home() / ".openviking"

OPENVIKING_CONFIG_ENV = "OPENVIKING_CONFIG_FILE"
OPENVIKING_CLI_CONFIG_ENV = "OPENVIKING_CLI_CONFIG_FILE"

DEFAULT_OV_CONF = "ov.conf"
DEFAULT_OVCLI_CONF = "ovcli.conf"


def resolve_config_path(
    explicit_path: Optional[str],
    env_var: str,
    default_filename: str,
) -> Optional[Path]:
    """Resolve a config file path using the three-level chain.

    Resolution order:
      1. ``explicit_path`` (if provided and exists)
      2. Path from environment variable ``env_var``
      3. ``~/.openviking/<default_filename>``

    Returns:
        Path to the config file, or None if not found at any level.
    """
    # Level 1: explicit path
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if p.exists():
            return p
        return None

    # Level 2: environment variable
    env_val = os.environ.get(env_var)
    if env_val:
        p = Path(env_val).expanduser()
        if p.exists():
            return p
        return None

    # Level 3: default directory
    p = DEFAULT_CONFIG_DIR / default_filename
    if p.exists():
        return p

    return None


def load_json_config(path: Path) -> Dict[str, Any]:
    """Load and parse a JSON config file.

    Args:
        path: Path to the JSON config file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file contains invalid JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")

    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file {path}: {e}") from e


def require_config(
    explicit_path: Optional[str],
    env_var: str,
    default_filename: str,
    purpose: str,
) -> Dict[str, Any]:
    """Resolve and load a config file, raising a clear error if not found.

    Args:
        explicit_path: Explicitly provided config file path.
        env_var: Environment variable name for the config path.
        default_filename: Default filename under ~/.openviking/.
        purpose: Human-readable description for error messages.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: With a clear message if the config file is not found.
    """
    path = resolve_config_path(explicit_path, env_var, default_filename)
    if path is None:
        default_path = DEFAULT_CONFIG_DIR / default_filename
        raise FileNotFoundError(
            f"OpenViking {purpose} configuration file not found.\n"
            f"Please create {default_path} or set {env_var}.\n"
            f"See: https://openviking.dev/docs/guides/configuration"
        )
    return load_json_config(path)
