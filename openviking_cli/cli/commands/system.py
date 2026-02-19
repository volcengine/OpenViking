# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""System utility commands."""

from typing import Optional

import typer

from openviking_cli.cli.errors import run


def register(app: typer.Typer) -> None:
    """Register system utility commands."""

    @app.command("wait")
    def wait_command(
        ctx: typer.Context,
        timeout: Optional[float] = typer.Option(None, help="Wait timeout in seconds"),
    ) -> None:
        """Wait for queued async processing to complete."""
        run(ctx, lambda client: client.wait_processed(timeout))
