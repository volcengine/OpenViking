# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Relation management commands."""

from typing import List

import typer

from openviking_cli.cli.context import get_cli_context
from openviking_cli.cli.errors import execute_client_command, run
from openviking_cli.cli.output import output_success


def register(app: typer.Typer) -> None:
    """Register relation commands."""

    @app.command("relations")
    def relations_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI"),
    ) -> None:
        """List relations of a resource."""
        run(ctx, lambda client: client.relations(uri))

    @app.command("link")
    def link_command(
        ctx: typer.Context,
        from_uri: str = typer.Argument(..., help="Source URI"),
        to_uris: List[str] = typer.Argument(..., help="One or more target URIs"),
        reason: str = typer.Option("", "--reason", help="Reason for linking"),
    ) -> None:
        """Create relation links from one URI to one or more targets."""
        cli_ctx = get_cli_context(ctx)
        result = execute_client_command(
            cli_ctx,
            lambda client: client.link(from_uri, to_uris, reason),
        )
        output_success(
            cli_ctx,
            result if result is not None else {"from": from_uri, "to": to_uris, "reason": reason},
        )

    @app.command("unlink")
    def unlink_command(
        ctx: typer.Context,
        from_uri: str = typer.Argument(..., help="Source URI"),
        to_uri: str = typer.Argument(..., help="Target URI to unlink"),
    ) -> None:
        """Remove a relation link."""
        cli_ctx = get_cli_context(ctx)
        result = execute_client_command(
            cli_ctx,
            lambda client: client.unlink(from_uri, to_uri),
        )
        output_success(
            cli_ctx,
            result if result is not None else {"from": from_uri, "to": to_uri},
        )
