# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight entry point for the optional VikingBot component."""

import importlib
import sys
from typing import Callable


def _load_app() -> Callable[[], None]:
    return importlib.import_module("vikingbot.cli.commands").app


def main() -> None:
    """Run VikingBot or explain how to install its optional dependencies."""
    try:
        app = _load_app()
    except ModuleNotFoundError as exc:
        missing_module = exc.name or "unknown"
        print(
            "Error: VikingBot optional dependencies are not installed.\n"
            f"Missing Python module: {missing_module}\n\n"
            "Install VikingBot with:\n"
            '  uv tool install --force "openviking[bot]"\n'
            "Or, inside an existing environment:\n"
            '  uv pip install "openviking[bot]"',
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    app()
