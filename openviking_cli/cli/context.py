# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Runtime context and client factory for CLI commands."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import typer

if TYPE_CHECKING:
    from openviking_cli.client.sync_http import SyncHTTPClient


class CliConfigError(ValueError):
    """Raised when required CLI configuration is missing or invalid."""


@dataclass
class CLIContext:
    """Shared state for one CLI invocation."""

    compact: bool = True
    output_format: str = "table"
    _client: Optional["SyncHTTPClient"] = field(default=None, init=False, repr=False)

    def get_client_http_only(self) -> "SyncHTTPClient":
        """Create an HTTP client (auto-loads ovcli.conf)."""
        if self._client is not None:
            return self._client

        from openviking_cli.client.sync_http import SyncHTTPClient

        try:
            self._client = SyncHTTPClient()
        except ValueError as e:
            raise CliConfigError(str(e)) from e
        self._client.initialize()
        return self._client

    def close_client(self) -> None:
        """Close the client if it has been created."""
        if self._client is None:
            return
        self._client.close()
        self._client = None


def get_cli_context(ctx: typer.Context) -> CLIContext:
    """Return a typed CLI context from Typer context."""
    if not isinstance(ctx.obj, CLIContext):
        raise RuntimeError("CLI context is not initialized")
    return ctx.obj
