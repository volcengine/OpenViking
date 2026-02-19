# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Command registration for OpenViking CLI."""

import typer

from openviking_cli.cli.commands import (
    content,
    debug,
    filesystem,
    observer,
    pack,
    relations,
    resources,
    search,
    serve,
    session,
    system,
)


def register_commands(app: typer.Typer) -> None:
    """Register all supported commands into the root CLI app."""
    serve.register(app)
    resources.register(app)
    filesystem.register(app)
    content.register(app)
    search.register(app)
    relations.register(app)
    pack.register(app)
    system.register(app)
    debug.register(app)
    observer.register(app)
    session.register(app)
