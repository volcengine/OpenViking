# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight entry point for openviking-server.

This module lives outside the ``openviking`` package so that importing it
does NOT trigger ``openviking/__init__.py`` (which eagerly imports clients
and initialises the config singleton via module-level loggers).

The real bootstrap logic stays in ``openviking.server.bootstrap``; we just
pre-parse ``--config`` and set the environment variable before that module
is ever imported.

Subcommands ``init`` and ``doctor`` are handled here directly (they don't
need a running server).
"""

import os
import sys

from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV


def main():
    # Intercept subcommands that don't need the server.
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from openviking_cli.setup_wizard import main as init_main

        sys.exit(init_main())

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from openviking_cli.doctor import main as doctor_main

        sys.exit(doctor_main())

    # Pre-parse --config from sys.argv before any openviking imports,
    # so the env var is visible when the config singleton first initialises.
    for i, arg in enumerate(sys.argv):
        if arg == "--config" and i + 1 < len(sys.argv):
            os.environ[OPENVIKING_CONFIG_ENV] = sys.argv[i + 1]
            break
        if arg.startswith("--config="):
            os.environ[OPENVIKING_CONFIG_ENV] = arg.split("=", 1)[1]
            break

    from openviking.server.bootstrap import main as _real_main

    _real_main()


if __name__ == "__main__":
    main()
