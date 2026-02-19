# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Import/export pack commands."""

import typer

from openviking_cli.cli.errors import run


def register(app: typer.Typer) -> None:
    """Register pack commands."""

    @app.command("export")
    def export_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Source URI"),
        to: str = typer.Argument(..., help="Output .ovpack file path"),
    ) -> None:
        """Export context as .ovpack."""
        run(ctx, lambda client: {"file": client.export_ovpack(uri, to)})

    @app.command("import")
    def import_command(
        ctx: typer.Context,
        file_path: str = typer.Argument(..., help="Input .ovpack file path"),
        target_uri: str = typer.Argument(..., help="Target parent URI"),
        force: bool = typer.Option(False, "--force", help="Overwrite when conflicts exist"),
        no_vectorize: bool = typer.Option(
            False,
            "--no-vectorize",
            help="Disable vectorization after import",
        ),
    ) -> None:
        """Import .ovpack into target URI."""
        run(
            ctx,
            lambda client: {
                "uri": client.import_ovpack(
                    file_path=file_path,
                    target=target_uri,
                    force=force,
                    vectorize=not no_vectorize,
                )
            },
        )
