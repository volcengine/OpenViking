# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Observer commands."""

import typer

from openviking_cli.cli.errors import run

observer_app = typer.Typer(help="Observer status commands")


@observer_app.command("queue")
def observer_queue_command(ctx: typer.Context) -> None:
    """Get queue status."""
    run(ctx, lambda client: client.observer.queue)


@observer_app.command("vikingdb")
def observer_vikingdb_command(ctx: typer.Context) -> None:
    """Get VikingDB status."""
    run(ctx, lambda client: client.observer.vikingdb)


@observer_app.command("vlm")
def observer_vlm_command(ctx: typer.Context) -> None:
    """Get VLM status."""
    run(ctx, lambda client: client.observer.vlm)


@observer_app.command("system")
def observer_system_command(ctx: typer.Context) -> None:
    """Get overall system status."""
    run(ctx, lambda client: client.observer.system)


def register(app: typer.Typer) -> None:
    """Register observer command group."""
    app.add_typer(observer_app, name="observer")
