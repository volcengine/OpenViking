# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""`serve` command implementation."""

import os
from typing import Optional

import typer

from openviking_cli.cli.context import get_cli_context
from openviking_cli.cli.errors import handle_command_error


def register(app: typer.Typer) -> None:
    """Register `serve` command."""

    @app.command("serve")
    def serve_command(
        ctx: typer.Context,
        host: Optional[str] = typer.Option(None, help="Host to bind to"),
        port: Optional[int] = typer.Option(None, help="Port to bind to"),
        config: Optional[str] = typer.Option(None, help="Path to ov.conf config file"),
    ) -> None:
        """Start OpenViking HTTP server."""
        cli_ctx = get_cli_context(ctx)

        try:
            import uvicorn

            from openviking.server.app import create_app
            from openviking.server.config import load_server_config

            if config is not None:
                os.environ["OPENVIKING_CONFIG_FILE"] = config

            server_config = load_server_config(config)
            if host is not None:
                server_config.host = host
            if port is not None:
                server_config.port = port

            app_instance = create_app(server_config)
            uvicorn.run(app_instance, host=server_config.host, port=server_config.port)
        except Exception as exc:  # noqa: BLE001
            handle_command_error(cli_ctx, exc)
