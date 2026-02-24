# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Content reading commands."""

import typer

from openviking_cli.cli.errors import run


def register(app: typer.Typer) -> None:
    """Register content commands."""

    @app.command("read")
    def read_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI"),
        offset: int = typer.Option(0, "--offset", "-s", help="Starting line number (0-indexed)"),
        limit: int = typer.Option(-1, "--limit", "-n", help="Number of lines to read (-1 = all)"),
    ) -> None:
        """Read full file content (L2)."""
        run(ctx, lambda client: client.read(uri, offset=offset, limit=limit))

    @app.command("abstract")
    def abstract_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI"),
    ) -> None:
        """Read abstract content (L0)."""
        run(ctx, lambda client: client.abstract(uri))

    @app.command("overview")
    def overview_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI"),
    ) -> None:
        """Read overview content (L1)."""
        run(ctx, lambda client: client.overview(uri))
