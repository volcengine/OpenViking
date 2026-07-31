# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared auth helpers for live OpenViking HTTP integration scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV


def root_api_key_from_ov_conf() -> str | None:
    """Read ``server.root_api_key`` from ov.conf, like benchmark preflight scripts."""
    try:
        config_path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
        if config_path is None:
            config_path = Path.home() / ".openviking" / DEFAULT_OV_CONF
        path = Path(config_path).expanduser()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        key = str((data.get("server") or {}).get("root_api_key") or "").strip()
        return key or None
    except Exception:
        return None


def resolve_api_key(cli_api_key: str | None = None) -> str | None:
    """Resolve the API key without requiring scripts to hardcode one.

    Priority:
    1. Explicit CLI value
    2. OPENVIKING_API_KEY
    3. OPENVIKING_ROOT_API_KEY
    4. server.root_api_key from ov.conf
    5. None, allowing SyncHTTPClient to load ~/.openviking/ovcli.conf
    """
    return (
        cli_api_key
        or os.environ.get("OPENVIKING_API_KEY")
        or os.environ.get("OPENVIKING_ROOT_API_KEY")
        or root_api_key_from_ov_conf()
    )


API_KEY_HELP = (
    "API key. Defaults to OPENVIKING_API_KEY / OPENVIKING_ROOT_API_KEY / "
    "server.root_api_key in ov.conf; when omitted, SyncHTTPClient may load "
    "~/.openviking/ovcli.conf."
)
