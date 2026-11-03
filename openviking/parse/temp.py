"""Helpers for parser-owned download and extraction temporary files."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def get_parser_download_temp_dir() -> Optional[str]:
    """Return the configured parser download directory, if configured."""
    try:
        from openviking_cli.utils.config import get_openviking_config

        configured = get_openviking_config().parser.download_temp_dir
    except Exception:
        return None

    if not configured:
        return None
    root = Path(configured).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return str(root)
