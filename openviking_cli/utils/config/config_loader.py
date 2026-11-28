# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration file loading utilities.

Provides a four-level resolution chain for locating config files:
  1. Explicit path (constructor parameter / --config)
  2. Environment variable
  3. Default path (~/.openviking/)
  4. System path (/etc/openviking/)
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .consts import (
    DEFAULT_CONFIG_DIR,
    SYSTEM_CONFIG_DIR,
)

_JSON_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\$\{[^}]*\}|%[^%]*%|[{}\[\],:]')
_REPORTING_PATH = ("server", "observability", "reporting")


def expand_config_environment(raw: str) -> str:
    """Expand config references while preserving inline KMS reporting literals."""
    # Locate strings by JSON path without parsing the full document first:
    # other sections may use unquoted environment references for numeric values.
    frames: list[tuple[tuple[str | None, ...], bool, str | None]] = []
    parts: list[str] = []
    offset = 0
    for match in _JSON_TOKENS.finditer(raw):
        token = match.group()
        path, is_object, key = frames[-1] if frames else ((), False, None)
        if token.startswith('"'):
            if is_object and key is None:
                try:
                    key = json.loads(token)
                except json.JSONDecodeError:
                    # Preserve the original expansion/validation behavior for
                    # templates whose keys become valid JSON after expansion.
                    key = ""
                frames[-1] = (path, is_object, key)
            elif is_object:
                path += (key,)
            if path[: len(_REPORTING_PATH)] == _REPORTING_PATH:
                # JSON decoding restores the exact dollars/percents afterwards.
                # Neither POSIX nor Windows expandvars can reinterpret them.
                parts.extend(
                    (
                        raw[offset : match.start()],
                        token.replace("$", r"\u0024").replace("%", r"\u0025"),
                    )
                )
                offset = match.end()
        elif token in ("{", "["):
            if frames:
                path += (key if is_object else None,)
            frames.append((path, token == "{", None))
        elif token in ("}", "]"):
            if frames:
                frames.pop()
        elif token == "," and is_object:
            frames[-1] = (path, is_object, None)
    parts.append(raw[offset:])
    return os.path.expandvars("".join(parts))


def resolve_config_path(
    explicit_path: Optional[str],
    env_var: str,
    default_filename: str,
) -> Optional[Path]:
    """Resolve a config file path using the four-level chain.

    Resolution order:
      1. ``explicit_path`` (if provided and exists)
      2. Path from environment variable ``env_var``
      3. ``~/.openviking/<default_filename>``
      4. ``/etc/openviking/<default_filename>``

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

    # Level 3: default directory (~/.openviking)
    p = DEFAULT_CONFIG_DIR / default_filename
    if p.exists():
        return p

    # Level 4: system directory (/etc/openviking)
    p = SYSTEM_CONFIG_DIR / default_filename
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
        ValueError: If the file contains invalid JSON or its root is not an object.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")

    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()

    raw = expand_config_environment(raw)

    try:
        # print(f"Loading config file: {path}")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"Config file {path} must contain a JSON object, got {type(data).__name__}"
        )
    return data


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
        default_path_user = DEFAULT_CONFIG_DIR / default_filename
        default_path_system = SYSTEM_CONFIG_DIR / default_filename
        raise FileNotFoundError(
            f"OpenViking {purpose} configuration file not found.\n"
            f"Please create {default_path_user} or {default_path_system}, or set {env_var}.\n"
            f"See: https://openviking.ai/docs"
        )
    return load_json_config(path)
