# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Exception handling helpers for CLI commands."""

from typing import Any, Callable

import httpx
import typer

from openviking_cli.cli.context import CliConfigError, CLIContext, get_cli_context
from openviking_cli.cli.output import output_error, output_success
from openviking_cli.exceptions import OpenVikingError


def handle_command_error(ctx: CLIContext, exc: Exception) -> None:
    """Normalize command exceptions into user-facing output and exit codes."""
    if isinstance(exc, typer.Exit):
        raise exc

    if isinstance(exc, CliConfigError):
        output_error(
            ctx,
            message=str(exc),
            code="CLI_CONFIG",
            details={"config_file": "ovcli.conf"},
            exit_code=2,
        )

    elif isinstance(exc, OpenVikingError):
        output_error(
            ctx,
            message=exc.message,
            code=exc.code,
            details=exc.details,
            exit_code=1,
        )

    elif isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        output_error(
            ctx,
            message=(
                "Failed to connect to OpenViking server. "
                "Check the url in ovcli.conf and ensure the server is running."
            ),
            code="CONNECTION_ERROR",
            exit_code=3,
            details={"exception": str(exc)},
        )

    else:
        output_error(
            ctx,
            message=str(exc),
            code="CLI_ERROR",
            exit_code=1,
            details={"exception": type(exc).__name__},
        )


def execute_client_command(
    ctx: CLIContext,
    operation: Callable[[Any], Any],
) -> Any:
    """Run a client command with consistent error handling and cleanup."""
    try:
        client = ctx.get_client_http_only()
        return operation(client)
    except Exception as exc:  # noqa: BLE001
        handle_command_error(ctx, exc)
    finally:
        ctx.close_client()


def run(
    ctx: typer.Context,
    fn: Callable[[Any], Any],
) -> None:
    """Execute a client command with boilerplate: context → execute → output."""
    cli_ctx = get_cli_context(ctx)
    result = execute_client_command(cli_ctx, fn)
    output_success(cli_ctx, result)
