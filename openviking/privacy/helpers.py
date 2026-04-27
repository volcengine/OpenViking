# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for user privacy config paths and version handling."""

import json
import re
from typing import Any

_VERSION_RE = re.compile(r"^version_(\d+)\.json$")


def config_root_uri(user_space: str, category: str, target_key: str) -> str:
    return f"viking://user/{user_space}/privacy/{category}/{target_key}"


def current_uri(user_space: str, category: str, target_key: str) -> str:
    return f"{config_root_uri(user_space, category, target_key)}/current.json"


def meta_uri(user_space: str, category: str, target_key: str) -> str:
    return f"{config_root_uri(user_space, category, target_key)}/.meta.json"


def history_dir_uri(user_space: str, category: str, target_key: str) -> str:
    return f"{config_root_uri(user_space, category, target_key)}/history"


def version_uri(user_space: str, category: str, target_key: str, version: int) -> str:
    return f"{history_dir_uri(user_space, category, target_key)}/{version_filename(version)}"


def version_filename(version: int) -> str:
    return f"version_{version:03d}.json"


def parse_version_filename(name: str) -> int | None:
    match = _VERSION_RE.match(name)
    if not match:
        return None
    return int(match.group(1))


def canonicalize_values(values: dict[str, Any]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
