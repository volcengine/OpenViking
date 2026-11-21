# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared primitives for Jev-compatible System One endpoints."""

import time
from typing import Dict, Tuple

import httpx

MODE_NOUL = "noul"
MODE_CHOICE = "choice"
DEFAULT_SYSTEM_ONE_API_BASE = "https://api.typesafe.ai"
DEFAULT_SYSTEM_ONE_URL = f"{DEFAULT_SYSTEM_ONE_API_BASE}/v1/systemone"


def resolve_jev_target(config) -> Tuple[str, str, Dict[str, str]]:
    """Resolve a System One endpoint, model, and headers from supported configs."""
    if hasattr(config, "resolved_api_base"):
        return (
            config.resolved_api_base(),
            config.resolved_model(),
            config.resolved_extra_headers(),
        )

    api_base = config.api_base or DEFAULT_SYSTEM_ONE_API_BASE
    default_model = "typesafe-ai/jev" if "ai-gateway.vercel.sh" in api_base else "jev-latest"
    return api_base, config.model or default_model, dict(config.extra_headers or {})


class SystemOneTransport:
    """Shared System One HTTP transport; response policy belongs to each caller."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        timeout: float,
        extra_headers: Dict[str, str] | None = None,
    ):
        self.model = model
        self.api_url = (
            api_base.rstrip("/")
            if api_base.rstrip("/").endswith("/v1/systemone")
            else f"{api_base.rstrip('/')}/v1/systemone"
        )
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **dict(extra_headers or {}),
            },
            timeout=timeout,
        )

    def request(self, *, state: dict, questions: dict) -> tuple[dict, float, int]:
        started = time.monotonic()
        response = self._client.post(
            self.api_url,
            json={"model": self.model, "state": state, "questions": questions},
        )
        duration = time.monotonic() - started
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("System One response must be a JSON object")
        return data, duration, response.status_code

    def close(self) -> None:
        self._client.close()


def validate_jev_probability(value: object, *, label: str = "Jev probability") -> float:
    """Validate and normalize one probability returned by System One."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"Invalid {label}: {value!r}")
    return float(value)
