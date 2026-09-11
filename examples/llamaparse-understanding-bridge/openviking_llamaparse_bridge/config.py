# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Environment configuration for the LlamaParse bridge."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit

REGION_URLS = {
    "na": "https://api.cloud.llamaindex.ai",
    "eu": "https://api.cloud.eu.llamaindex.ai",
}
SUPPORTED_TIERS = {"cost_effective", "agentic", "agentic_plus"}


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _boolean(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _integer(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    try:
        value = int(raw) if raw and raw.strip() else default
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _http_url(value: str, name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain a query or fragment")


def _json_object(environ: Mapping[str, str], name: str) -> Dict[str, Any]:
    raw = environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    reserved = {"file_id", "source_url", "tier", "version"}
    conflicts = sorted(reserved.intersection(value))
    if conflicts:
        names = ", ".join(conflicts)
        raise ValueError(f"{name} cannot set bridge-managed fields: {names}")
    return value


def _validate_parse_options(parse_options: Mapping[str, Any]) -> None:
    for name in ("output_options", "processing_options"):
        if name in parse_options and not isinstance(parse_options[name], dict):
            raise ValueError(f"LLAMAPARSE_PARSE_OPTIONS_JSON.{name} must be an object")

    output_options = parse_options.get("output_options")
    if not isinstance(output_options, dict):
        return
    if "images_to_save" in output_options and not isinstance(
        output_options["images_to_save"], list
    ):
        raise ValueError(
            "LLAMAPARSE_PARSE_OPTIONS_JSON.output_options.images_to_save must be an array"
        )


@dataclass(frozen=True)
class Settings:
    """Validated bridge settings."""

    llama_api_key: str
    bridge_api_key: str
    llama_base_url: str = REGION_URLS["na"]
    tier: str = "agentic"
    version: str = "latest"
    cost_optimizer: bool = True
    public_url: str = "http://127.0.0.1:8080"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    timeout_seconds: int = 120
    artifact_ttl_seconds: int = 300
    artifact_cache_dir: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "openviking-llamaparse-bridge"
    )
    artifact_cache_max_bytes: int = 1024 * 1024 * 1024
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    parse_options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.llama_api_key.strip():
            raise ValueError("LLAMA_CLOUD_API_KEY is required")
        if not self.bridge_api_key.strip():
            raise ValueError("PARSER_BRIDGE_API_KEY is required")
        if len(self.bridge_api_key) < 32:
            raise ValueError("PARSER_BRIDGE_API_KEY must contain at least 32 characters")
        if self.tier not in SUPPORTED_TIERS:
            choices = ", ".join(sorted(SUPPORTED_TIERS))
            raise ValueError(f"LLAMAPARSE_TIER must be one of: {choices}")
        if self.cost_optimizer and self.tier not in {"agentic", "agentic_plus"}:
            raise ValueError("LLAMAPARSE_COST_OPTIMIZER requires agentic or agentic_plus tier")
        _http_url(self.llama_base_url, "LLAMAPARSE_BASE_URL")
        _http_url(self.public_url, "BRIDGE_PUBLIC_URL")
        if not 1 <= self.bind_port <= 65535:
            raise ValueError("BRIDGE_PORT must be between 1 and 65535")
        if self.timeout_seconds <= 0:
            raise ValueError("BRIDGE_HTTP_TIMEOUT_SECONDS must be greater than zero")
        if self.artifact_ttl_seconds <= 0:
            raise ValueError("BRIDGE_ARTIFACT_TTL_SECONDS must be greater than zero")
        if self.artifact_cache_max_bytes <= 0:
            raise ValueError("BRIDGE_ARTIFACT_CACHE_MAX_BYTES must be greater than zero")
        _validate_parse_options(self.parse_options)


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Load settings from environment variables."""
    env = environ if environ is not None else os.environ
    region = env.get("LLAMAPARSE_REGION", "na").strip().lower() or "na"
    base_url = env.get("LLAMAPARSE_BASE_URL", "").strip()
    if not base_url:
        try:
            base_url = REGION_URLS[region]
        except KeyError as exc:
            raise ValueError("LLAMAPARSE_REGION must be na or eu") from exc

    return Settings(
        llama_api_key=_required(env, "LLAMA_CLOUD_API_KEY"),
        bridge_api_key=_required(env, "PARSER_BRIDGE_API_KEY"),
        llama_base_url=base_url.rstrip("/"),
        tier=env.get("LLAMAPARSE_TIER", "agentic").strip().lower(),
        version=env.get("LLAMAPARSE_VERSION", "latest").strip() or "latest",
        cost_optimizer=_boolean(env, "LLAMAPARSE_COST_OPTIMIZER", True),
        public_url=env.get("BRIDGE_PUBLIC_URL", "http://127.0.0.1:8080").strip().rstrip("/"),
        bind_host=env.get("BRIDGE_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1",
        bind_port=_integer(env, "BRIDGE_PORT", 8080),
        timeout_seconds=_integer(env, "BRIDGE_HTTP_TIMEOUT_SECONDS", 120),
        artifact_ttl_seconds=_integer(env, "BRIDGE_ARTIFACT_TTL_SECONDS", 300),
        artifact_cache_dir=Path(
            env.get("BRIDGE_ARTIFACT_CACHE_DIR", "").strip()
            or Path(tempfile.gettempdir()) / "openviking-llamaparse-bridge"
        ),
        artifact_cache_max_bytes=_integer(
            env, "BRIDGE_ARTIFACT_CACHE_MAX_BYTES", 1024 * 1024 * 1024
        ),
        organization_id=env.get("LLAMAPARSE_ORGANIZATION_ID", "").strip() or None,
        project_id=env.get("LLAMAPARSE_PROJECT_ID", "").strip() or None,
        parse_options=_json_object(env, "LLAMAPARSE_PARSE_OPTIONS_JSON"),
    )
