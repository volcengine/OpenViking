# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Typer entrypoint for OpenViking CLI."""

from typing import Optional

import typer

from openviking.cli.commands import register_commands
from openviking.cli.context import CLIContext
from openviking.utils.config.config_loader import (
    DEFAULT_OVCLI_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
    resolve_config_path,
    load_json_config,
)

app = typer.Typer(
    help="OpenViking - An Agent-native context database",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        from openviking import __version__

        typer.echo(f"openviking {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Compact JSON with {ok, result} wrapper (for scripts)"
    ),
    output_format: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output format: table (default), json"
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Configure shared CLI options."""
    # Priority: --output CLI arg > ovcli.conf "output" field > default "table"
    if output_format is None:
        config_path = resolve_config_path(None, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
        if config_path is not None:
            try:
                cfg = load_json_config(config_path)
                output_format = cfg.get("output")
            except (ValueError, FileNotFoundError):
                pass
        if output_format is None:
            output_format = "table"

    ctx.obj = CLIContext(json_output=json_output, output_format=output_format)


register_commands(app)


if __name__ == "__main__":
    app()
