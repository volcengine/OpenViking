# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Resource management commands."""

from pathlib import Path
from typing import Optional

import typer

from openviking_cli.cli.errors import run


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
        timeout: Optional[float] = typer.Option(600.0, help="Wait timeout in seconds"),
    ) -> None:
        """Add resources into OpenViking."""
        # Validate path: if it's a local path, check if it exists
        final_path = path
        if not (path.startswith("http://") or path.startswith("https://")):
            unescaped_path = path.replace("\\ ", " ")
            local_path = Path(unescaped_path)
            final_path = unescaped_path
            if not local_path.exists():
                # Check if there are extra arguments (possible unquoted path with spaces)
                import sys

                # Find the index of 'add-resource' in sys.argv
                try:
                    add_resource_idx = sys.argv.index("add-resource")
                except ValueError:
                    add_resource_idx = sys.argv.index("add") if "add" in sys.argv else -1

                if add_resource_idx != -1 and len(sys.argv) > add_resource_idx + 2:
                    # There are extra positional arguments - likely unquoted path with spaces
                    extra_args = sys.argv[add_resource_idx + 2 :]
                    suggested_path = f"{path} {' '.join(extra_args)}"
                    typer.echo(
                        typer.style(
                            f"Error: Path '{path}' does not exist.",
                            fg=typer.colors.RED,
                            bold=True,
                        ),
                        err=True,
                    )
                    typer.echo(
                        typer.style(
                            "\nIt looks like you may have forgotten to quote a path with spaces.",
                            fg=typer.colors.YELLOW,
                        ),
                        err=True,
                    )
                    typer.echo(
                        typer.style(
                            f'Suggested command: ov add-resource "{suggested_path}"',
                            fg=typer.colors.CYAN,
                        ),
                        err=True,
                    )
                    raise typer.Exit(code=1)
                else:
                    typer.echo(
                        typer.style(
                            f"Error: Path '{path}' does not exist.", fg=typer.colors.RED, bold=True
                        ),
                        err=True,
                    )
                    raise typer.Exit(code=1)

        run(
            ctx,
            lambda client: client.add_resource(
                path=final_path,
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
        timeout: Optional[float] = typer.Option(600.0, help="Wait timeout in seconds"),
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
