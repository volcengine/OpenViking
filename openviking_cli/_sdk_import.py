# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path


def import_openviking_sdk():
    sdk_root = Path(__file__).resolve().parents[1] / "sdk" / "python"
    if (sdk_root / "openviking_sdk").is_dir() and str(sdk_root) not in sys.path:
        sys.path.insert(0, str(sdk_root))
    try:
        return import_module("openviking_sdk")
    except ImportError as exc:
        try:
            return import_module("openviking_sdk")
        except ImportError:
            raise ImportError(
                "openviking-sdk is required for HTTP client usage. "
                "Install it with the main package or run 'pip install -e sdk/python' "
                "when developing from the repository."
            ) from exc
