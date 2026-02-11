# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Filesystem commands."""

import typer

from openviking.cli.errors import run


def register(app: typer.Typer) -> None:
    """Register filesystem commands."""

    @app.command("ls")
    def ls_command(
        ctx: typer.Context,
        uri: str = typer.Argument("viking://", help="Viking URI"),
        simple: bool = typer.Option(False, "--simple", "-s", help="Simple path output"),
        recursive: bool = typer.Option(
            False,
            "--recursive",
            "-r",
            help="List all subdirectories recursively",
        ),
    ) -> None:
        """List directory contents."""
        run(ctx, lambda client: client.ls(uri, simple=simple, recursive=recursive))

    @app.command("tree")
    def tree_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI"),
    ) -> None:
        """Get directory tree."""
        run(ctx, lambda client: client.tree(uri))

    @app.command("mkdir")
    def mkdir_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Directory URI to create"),
    ) -> None:
        """Create a directory."""
        run(ctx, lambda client: client.mkdir(uri))

    @app.command("rm")
    def rm_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI to remove"),
        recursive: bool = typer.Option(False, "--recursive", "-r", help="Remove recursively"),
    ) -> None:
        """Remove a resource."""
        run(ctx, lambda client: client.rm(uri, recursive=recursive))

    @app.command("mv")
    def mv_command(
        ctx: typer.Context,
        from_uri: str = typer.Argument(..., help="Source URI"),
        to_uri: str = typer.Argument(..., help="Target URI"),
    ) -> None:
        """Move or rename a resource."""
        run(ctx, lambda client: client.mv(from_uri, to_uri))

    @app.command("stat")
    def stat_command(
        ctx: typer.Context,
        uri: str = typer.Argument(..., help="Viking URI"),
    ) -> None:
        """Get resource metadata and status."""
        run(ctx, lambda client: client.stat(uri))
