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
    ) -> None:
        """Read full file content (L2)."""
        run(ctx, lambda client: client.read(uri))

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
