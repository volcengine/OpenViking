# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Session commands."""

import typer

from openviking_cli.cli.context import get_cli_context
from openviking_cli.cli.errors import execute_client_command, run
from openviking_cli.cli.output import output_success

session_app = typer.Typer(help="Session management commands")


@session_app.command("new")
def session_new_command(
    ctx: typer.Context,
) -> None:
    """Create a new session."""
    run(ctx, lambda client: client.create_session())


@session_app.command("list")
def session_list_command(ctx: typer.Context) -> None:
    """List sessions."""
    run(ctx, lambda client: client.list_sessions())


@session_app.command("get")
def session_get_command(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID"),
) -> None:
    """Get session details."""
    run(ctx, lambda client: client.get_session(session_id))


@session_app.command("delete")
def session_delete_command(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID"),
) -> None:
    """Delete a session."""
    cli_ctx = get_cli_context(ctx)
    result = execute_client_command(cli_ctx, lambda client: client.delete_session(session_id))
    output_success(cli_ctx, result if result is not None else {"session_id": session_id})


@session_app.command("add-message")
def session_add_message_command(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID"),
    role: str = typer.Option(..., "--role", help="Message role, e.g. user/assistant"),
    content: str = typer.Option(..., "--content", help="Message content"),
) -> None:
    """Add one message to a session."""
    run(ctx, lambda client: client.add_message(session_id=session_id, role=role, content=content))


@session_app.command("commit")
def session_commit_command(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID"),
) -> None:
    """Commit a session (archive messages and extract memories)."""
    run(ctx, lambda client: client.commit_session(session_id))


def register(app: typer.Typer) -> None:
    """Register session command group."""
    app.add_typer(session_app, name="session")
