# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""`mcp` command implementation."""

from typing import Optional

import typer

from openviking_cli.cli.context import get_cli_context
from openviking_cli.cli.errors import handle_command_error


def register(app: typer.Typer) -> None:
    """Register `mcp` command."""

    @app.command("mcp")
    def mcp_command(
        ctx: typer.Context,
        path: str = typer.Option(
            ...,
            "--path",
            help="Local workspace path for embedded OpenViking storage",
        ),
        config: Optional[str] = typer.Option(
            None,
            "--config",
            help="Path to ov.conf config file",
        ),
        transport: str = typer.Option(
            "stdio",
            "--transport",
            help="MCP transport mode. MVP supports only 'stdio'.",
        ),
    ) -> None:
        """Run OpenViking MCP server (stdio, embedded mode)."""
        cli_ctx = get_cli_context(ctx)
        try:
            if transport != "stdio":
                raise ValueError("Only stdio transport is supported in MVP")
            from openviking.mcp.server import run_stdio_server

            run_stdio_server(path=path, config=config, transport=transport)
        except Exception as exc:  # noqa: BLE001
            handle_command_error(cli_ctx, exc)
