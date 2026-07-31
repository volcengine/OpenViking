# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Environment-driven configuration for the OpenWebUI tool server."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip() or default


@dataclass(frozen=True)
class Settings:
    """Settings resolved from environment variables.

    Read once at process start. Override by exporting variables before
    launching the server.
    """

    endpoint: str
    api_key: str
    account: str
    user: str
    agent: str
    bind: str
    timeout_seconds: float

    @property
    def bind_host(self) -> str:
        host, _, _ = self.bind.partition(":")
        return host or "0.0.0.0"

    @property
    def bind_port(self) -> int:
        _, _, port = self.bind.partition(":")
        try:
            return int(port) if port else 8765
        except ValueError:
            return 8765

    @property
    def memories_uri(self) -> str:
        """Conventional URI prefix where personal memories live."""
        return "viking://user/memories/"


def load_settings() -> Settings:
    """Build a Settings instance from process env."""
    return Settings(
        endpoint=_env("OV_ENDPOINT", "http://localhost:1933").rstrip("/"),
        api_key=_env("OV_API_KEY", ""),
        account=_env("OV_ACCOUNT", "default"),
        user=_env("OV_USER", "default"),
        agent=_env("OV_AGENT", "default"),
        bind=_env("OV_BIND", "0.0.0.0:8765"),
        timeout_seconds=float(_env("OV_TIMEOUT", "30")),
    )
