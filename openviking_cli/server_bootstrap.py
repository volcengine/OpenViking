# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight entry point for openviking-server.

This module lives outside the ``openviking`` package so that importing it
does NOT trigger ``openviking/__init__.py`` (which eagerly imports clients
and initialises the config singleton via module-level loggers).

The real bootstrap logic stays in ``openviking.server.bootstrap``; we just
pre-parse ``--config`` and set the environment variable before that module
is ever imported.
"""

import os
import sys


def main():
    # Pre-parse --config from sys.argv before any openviking imports,
    # so the env var is visible when the config singleton first initialises.
    for i, arg in enumerate(sys.argv):
        if arg == "--config" and i + 1 < len(sys.argv):
            os.environ["OPENVIKING_CONFIG_FILE"] = sys.argv[i + 1]
            break
        if arg.startswith("--config="):
            os.environ["OPENVIKING_CONFIG_FILE"] = arg.split("=", 1)[1]
            break

    from openviking.server.bootstrap import main as _real_main

    _real_main()


if __name__ == "__main__":
    main()
