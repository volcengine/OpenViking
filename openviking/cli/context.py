# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Runtime context and client factory for CLI commands."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import typer

from openviking.utils.config.config_loader import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_OVCLI_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
    require_config,
)

if TYPE_CHECKING:
    from openviking.sync_client import SyncOpenViking


class CliConfigError(ValueError):
    """Raised when required CLI configuration is missing or invalid."""


def build_sync_client(**kwargs) -> "SyncOpenViking":
    """Create SyncOpenViking lazily to keep CLI import lightweight."""
    from openviking.sync_client import SyncOpenViking

    return SyncOpenViking(**kwargs)


@dataclass
class CLIContext:
    """Shared state for one CLI invocation."""

    json_output: bool = False
    output_format: str = "table"
    _client: Optional["SyncOpenViking"] = field(default=None, init=False, repr=False)

    def get_client_http_only(self) -> "SyncOpenViking":
        """Create an HTTP client from ovcli.conf."""
        if self._client is not None:
            return self._client

        try:
            cli_config = require_config(
                None,
                OPENVIKING_CLI_CONFIG_ENV,
                DEFAULT_OVCLI_CONF,
                "CLI",
            )
        except FileNotFoundError:
            default_path = DEFAULT_CONFIG_DIR / DEFAULT_OVCLI_CONF
            raise CliConfigError(
                f"CLI configuration file not found.\n"
                f"Please create {default_path} or set {OPENVIKING_CLI_CONFIG_ENV}.\n"
                f"Example content: "
                f'{{"url": "http://localhost:1933", "api_key": null}}'
            )

        url = cli_config.get("url")
        if not url:
            default_path = DEFAULT_CONFIG_DIR / DEFAULT_OVCLI_CONF
            raise CliConfigError(
                f'"url" is required in {default_path}.\n'
                f'Example: {{"url": "http://localhost:1933"}}'
            )

        self._client = build_sync_client(
            url=url,
            api_key=cli_config.get("api_key"),
            user=cli_config.get("user"),
        )
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
