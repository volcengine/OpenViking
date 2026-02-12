# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Debug and health commands."""

import typer

from openviking_cli.cli.context import get_cli_context
from openviking_cli.cli.errors import execute_client_command, run
from openviking_cli.cli.output import output_success


def register(app: typer.Typer) -> None:
    """Register debug commands."""

    @app.command("status")
    def status_command(ctx: typer.Context) -> None:
        """Show OpenViking component status."""
        run(ctx, lambda client: client.get_status())

    @app.command("health")
    def health_command(ctx: typer.Context) -> None:
        """Quick health check."""
        cli_ctx = get_cli_context(ctx)
        result = execute_client_command(cli_ctx, lambda client: client.is_healthy())
        output_success(cli_ctx, {"healthy": result})
        if not result:
            raise typer.Exit(1)
