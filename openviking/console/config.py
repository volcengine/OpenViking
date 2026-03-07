# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Configuration for the standalone OpenViking console service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List


def _parse_cors_origins(raw_value: str | None) -> List[str]:
    if not raw_value:
        return ["*"]
    return [item.strip() for item in raw_value.split(",") if item.strip()]


@dataclass(slots=True)
class ConsoleConfig:
    """Runtime settings for console BFF + static frontend."""

    host: str = "127.0.0.1"
    port: int = 8020
    openviking_base_url: str = "http://127.0.0.1:1933"
    write_enabled: bool = False
    request_timeout_sec: float = 30.0
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

    def normalized_base_url(self) -> str:
        """Return upstream base URL without trailing slash."""
        return self.openviking_base_url.rstrip("/")


def load_console_config(
    *,
    host: str = "127.0.0.1",
    port: int = 8020,
    openviking_base_url: str = "http://127.0.0.1:1933",
    write_enabled: bool = False,
    request_timeout_sec: float = 30.0,
    cors_origins: str | List[str] | None = None,
) -> ConsoleConfig:
    """Load console config from startup parameters."""
    resolved_cors_origins = (
        _parse_cors_origins(cors_origins)
        if isinstance(cors_origins, str) or cors_origins is None
        else list(cors_origins)
    )
    return ConsoleConfig(
        host=host,
        port=port,
        openviking_base_url=openviking_base_url,
        write_enabled=write_enabled,
        request_timeout_sec=request_timeout_sec,
        cors_origins=resolved_cors_origins,
    )


def as_runtime_capabilities(config: ConsoleConfig) -> dict:
    """Expose runtime behavior switches for UI gating."""
    allowed_modules: Iterable[str] = [
        "fs.read",
        "search.find",
        "admin.read",
        "monitor.read",
    ]
    if config.write_enabled:
        allowed_modules = [*allowed_modules, "fs.write", "admin.write", "resources.write"]

    return {
        "write_enabled": config.write_enabled,
        "allowed_modules": list(allowed_modules),
        "dangerous_actions": [
            "fs.mkdir",
            "fs.mv",
            "fs.rm",
            "admin.create_account",
            "admin.delete_account",
            "admin.create_user",
            "admin.delete_user",
            "admin.set_role",
            "admin.regenerate_key",
            "resources.add_resource",
        ],
    }
