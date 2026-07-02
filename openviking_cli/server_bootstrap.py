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


def _config_missing() -> bool:
    """True when no ov.conf resolves through the standard four-level chain."""
    from openviking_cli.utils.config.config_loader import resolve_config_path
    from openviking_cli.utils.config.consts import DEFAULT_OV_CONF

    return resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF) is None


def _maybe_offer_init() -> None:
    """On an interactive TTY with no config, offer to run the setup wizard.

    Falls through silently in non-interactive contexts (Docker, CI) so the
    server keeps its existing missing-config behavior there.
    """
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive or not _config_missing():
        return

    print("No OpenViking configuration found.")
    try:
        answer = input("Run interactive setup now? [Y/n]: ").strip().lower()
    except (EOFError, OSError):
        return
    if answer not in ("", "y", "yes"):
        return

    from openviking_cli.setup_wizard import main as init_main

    code = init_main()
    if code != 0 or _config_missing():
        sys.exit(code or 1)


def main():
    """Bootstrap the server while binding a stable execution-level log trace ID."""
    # Pre-parse --config from sys.argv before any openviking imports,
    # so the env var is visible when the config singleton first initialises.
    # This is done for all subcommands (init, doctor, server) to ensure
    # consistent behavior.
    for i, arg in enumerate(sys.argv):
        if arg == "--config" and i + 1 < len(sys.argv):
            os.environ[OPENVIKING_CONFIG_ENV] = sys.argv[i + 1]
            break
        if arg.startswith("--config="):
            os.environ[OPENVIKING_CONFIG_ENV] = arg.split("=", 1)[1]
            break

    # Import after config pre-parse to avoid early config singleton initialization via
    # module-level loggers.
    from openviking_cli.utils.logger import bind_log_execution_trace  # noqa: PLC0415

    # Intercept subcommands that don't need the server.
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from openviking_cli.setup_wizard import main as init_main

        with bind_log_execution_trace():
            sys.exit(init_main())

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from openviking_cli.doctor import main as doctor_main

        with bind_log_execution_trace():
            sys.exit(doctor_main())

    # `openviking-server ingest ...` runs the local-log ingestion CLI (client-side).
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        from openviking.ingest.cli import app as ingest_app

        with bind_log_execution_trace():
            ingest_app(args=sys.argv[2:], prog_name="openviking-server ingest")
        return

    if not any(arg in ("-h", "--help", "--version") for arg in sys.argv[1:]):
        _maybe_offer_init()

    from openviking.server.bootstrap import main as _real_main

    with bind_log_execution_trace():
        _real_main()


if __name__ == "__main__":
    main()
