# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Resource management commands."""

from typing import Optional

import typer

from openviking.cli.errors import run



def register(app: typer.Typer) -> None:
    """Register resource commands."""

    @app.command("add-resource")
    def add_resource_command(
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Local path or URL to import"),
        to: Optional[str] = typer.Option(None, "--to", help="Target URI"),
        reason: str = typer.Option("", help="Reason for import"),
        instruction: str = typer.Option("", help="Additional instruction"),
        wait: bool = typer.Option(False, "--wait", help="Wait until processing is complete"),
        timeout: Optional[float] = typer.Option(None, help="Wait timeout in seconds"),
    ) -> None:
        """Add resources into OpenViking."""
        run(
            ctx,
            lambda client: client.add_resource(
                path=path,
                target=to,
                reason=reason,
                instruction=instruction,
                wait=wait,
                timeout=timeout,
            ),
        )

    @app.command("add-skill")
    def add_skill_command(
        ctx: typer.Context,
        data: str = typer.Argument(..., help="Skill directory, SKILL.md, or raw content"),
        wait: bool = typer.Option(False, "--wait", help="Wait until processing is complete"),
        timeout: Optional[float] = typer.Option(None, help="Wait timeout in seconds"),
    ) -> None:
        """Add a skill into OpenViking."""
        run(
            ctx,
            lambda client: client.add_skill(
                data=data,
                wait=wait,
                timeout=timeout,
            ),
        )
