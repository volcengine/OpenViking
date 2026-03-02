# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Chat command - wrapper for vikingbot agent."""

import importlib.util
import shutil
import subprocess
import sys

import typer


def _check_vikingbot() -> bool:
    """Check if vikingbot is available."""
    return importlib.util.find_spec("vikingbot") is not None


def chat(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option(
        "cli__default__direct", "--session", "-s", help="Session ID"
    ),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show vikingbot runtime logs during chat"
    ),
):
    """
    Chat with vikingbot agent.

    This is equivalent to `vikingbot chat`.
    """
    if not _check_vikingbot():
        typer.echo(
            typer.style(
                "Error: vikingbot not found. Please install vikingbot first:",
                fg="red",
            )
        )
        typer.echo()
        typer.echo("  Option 1: Install from local source (recommended for development)")
        typer.echo("    cd bot")
        typer.echo("    uv pip install -e \".[dev]\"")
        typer.echo()
        typer.echo("  Option 2: Install from PyPI (coming soon)")
        typer.echo("    pip install vikingbot")
        typer.echo()
        raise typer.Exit(1)

    # Build the command arguments
    args = []

    if message:
        args.extend(["--message", message])
    args.extend(["--session", session_id])
    if not markdown:
        args.append("--no-markdown")
    if logs:
        args.append("--logs")

    # Check if vikingbot command exists
    vikingbot_path = shutil.which("vikingbot")

    if vikingbot_path:
        # Build the command: vikingbot chat [args...]
        full_args = [vikingbot_path, "chat"] + args
    else:
        # Fallback: use python -m
        full_args = [sys.executable, "-m", "vikingbot.cli.commands", "chat"] + args

    # Pass through all arguments to vikingbot agent
    try:
        subprocess.run(full_args, check=True)
    except subprocess.CalledProcessError as e:
        raise typer.Exit(e.returncode)


def register(app: typer.Typer) -> None:
    """Register chat command."""
    app.command("chat")(chat)
