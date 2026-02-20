# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Search commands."""

from typing import Optional

import typer

from openviking_cli.cli.errors import run


def register(app: typer.Typer) -> None:
    """Register search commands."""

    @app.command("find")
    def find_command(
        ctx: typer.Context,
        query: str = typer.Argument(..., help="Search query"),
        uri: str = typer.Option("", "--uri", "-u", help="Target URI"),
        limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of results"),
        threshold: Optional[float] = typer.Option(
            None,
            "--threshold",
            "-t",
            help="Score threshold",
        ),
    ) -> None:
        """Run semantic retrieval."""
        run(
            ctx,
            lambda client: client.find(
                query=query,
                target_uri=uri,
                limit=limit,
                score_threshold=threshold,
            ),
        )

    @app.command("search")
    def search_command(
        ctx: typer.Context,
        query: str = typer.Argument(..., help="Search query"),
        uri: str = typer.Option("", "--uri", "-u", help="Target URI"),
        session_id: Optional[str] = typer.Option(
            None,
            "--session-id",
            help="Session ID for context-aware search",
        ),
        limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of results"),
        threshold: Optional[float] = typer.Option(
            None,
            "--threshold",
            "-t",
            help="Score threshold",
        ),
    ) -> None:
        """Run context-aware retrieval."""
        run(
            ctx,
            lambda client: client.search(
                query=query,
                target_uri=uri,
                session_id=session_id,
                limit=limit,
                score_threshold=threshold,
            ),
        )

    @app.command("grep")
    def grep_command(
        ctx: typer.Context,
        uri: str = typer.Option("viking://", "--uri", "-u", help="Target URI"),
        pattern: str = typer.Argument(..., help="Search pattern"),
        ignore_case: bool = typer.Option(False, "--ignore-case", "-i", help="Case insensitive"),
    ) -> None:
        """Run content pattern search."""
        run(ctx, lambda client: client.grep(uri, pattern, case_insensitive=ignore_case))

    @app.command("glob")
    def glob_command(
        ctx: typer.Context,
        pattern: str = typer.Argument(..., help="Glob pattern"),
        uri: str = typer.Option("viking://", "--uri", "-u", help="Search root URI"),
    ) -> None:
        """Run file glob pattern search."""
        run(ctx, lambda client: client.glob(pattern, uri=uri))
