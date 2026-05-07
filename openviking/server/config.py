# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server configuration for OpenViking HTTP Server."""

import sys
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from openviking.server.identity import AuthMode
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.config_loader import (
    load_json_config,
    resolve_config_path,
)
from openviking_cli.utils.config.config_utils import format_validation_error
from openviking_cli.utils.config.consts import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_OV_CONF,
    OPENVIKING_CONFIG_ENV,
    SYSTEM_CONFIG_DIR,
)

logger = get_logger(__name__)


class MetricsAccountDimensionConfig(BaseModel):
    """Account-dimension configuration for metrics label injection."""

    # Enabled by default, but still allowlist-gated to avoid accidental high-cardinality exposure.
    enabled: bool = True
    max_active_accounts: int = 100
    metric_allowlist: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class PrometheusExporterConfig(BaseModel):
    """Prometheus exporter configuration."""

    enabled: bool = True

    model_config = {"extra": "forbid"}


class OTelExporterConfig(BaseModel):
    """OpenTelemetry exporter configuration."""

    class TLSConfig(BaseModel):
        """TLS configuration for OTLP exporters."""

        insecure: bool = False

        model_config = {"extra": "forbid"}

    enabled: bool = False
    protocol: str = "grpc"  # "grpc" or "http"
    tls: TLSConfig = Field(default_factory=TLSConfig)
    endpoint: str = "localhost:4317"  # gRPC default: 4317; HTTP default: 4318
    service_name: str = "openviking-server"
    export_interval_ms: int = 10000
    headers: Dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class MetricsExportersConfig(BaseModel):
    """Metrics exporters configuration."""

    prometheus: PrometheusExporterConfig = Field(default_factory=PrometheusExporterConfig)
    otel: OTelExporterConfig = Field(default_factory=OTelExporterConfig)

    model_config = {"extra": "forbid"}


class MetricsConfig(BaseModel):
    """Metrics subsystem configuration."""

    enabled: bool = False
    account_dimension: MetricsAccountDimensionConfig = Field(
        default_factory=MetricsAccountDimensionConfig
    )
    exporters: MetricsExportersConfig = Field(default_factory=MetricsExportersConfig)

    model_config = {"extra": "forbid"}


class ObservabilityConfig(BaseModel):
    """Server-side observability configuration."""

    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    traces: OTelExporterConfig = Field(default_factory=OTelExporterConfig)
    logs: OTelExporterConfig = Field(default_factory=OTelExporterConfig)

    model_config = {"extra": "forbid"}


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1933
    workers: int = 1
    auth_mode: Optional[AuthMode] = None  # If None, auto-detect based on root_api_key
    root_api_key: Optional[str] = None
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    with_bot: bool = False  # Enable Bot API proxy to Vikingbot
    bot_api_url: str = "http://localhost:18790"  # Vikingbot OpenAPIChannel URL (default port)
    encryption_enabled: bool = False  # Whether file-level AES encryption is enabled
    api_key_hashing_enabled: bool = False  # Whether API key Argon2id hashing is enabled (default: false, rely on file encryption)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    model_config = {"extra": "forbid"}

    def get_effective_auth_mode(self) -> AuthMode:
        """Get effective auth mode, auto-detecting if not explicitly set.

        - If root_api_key is configured (non-empty) and auth_mode is None: API_KEY
        - If root_api_key is not configured and auth_mode is None: DEV
        """
        if self.auth_mode is not None:
            return self.auth_mode
        if self.root_api_key is not None and self.root_api_key != "":
            return AuthMode.API_KEY
        return AuthMode.DEV


def load_server_config(config_path: Optional[str] = None) -> ServerConfig:
    """Load server configuration from ov.conf.

    Reads the ``server`` section of ov.conf and also ensures the full
    ov.conf is loaded into the OpenVikingConfigSingleton so that model
    and storage settings are available.

    Resolution chain:
      1. Explicit ``config_path`` (from --config)
      2. OPENVIKING_CONFIG_FILE environment variable
      3. ~/.openviking/ov.conf

    Args:
        config_path: Explicit path to ov.conf.

    Returns:
        ServerConfig instance with defaults for missing fields.

    Raises:
        FileNotFoundError: If no config file is found.
    """
    path = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if path is None:
        default_path_user = DEFAULT_CONFIG_DIR / DEFAULT_OV_CONF
        default_path_system = SYSTEM_CONFIG_DIR / DEFAULT_OV_CONF
        raise FileNotFoundError(
            f"OpenViking configuration file not found.\n"
            f"Please create {default_path_user} or {default_path_system}, or set {OPENVIKING_CONFIG_ENV}.\n"
            f"See: https://openviking.ai/docs"
        )

    data = load_json_config(path)
    server_data = data.get("server", {})
    if server_data is None:
        server_data = {}
    if not isinstance(server_data, dict):
        raise ValueError("Invalid server config: 'server' section must be an object")

    # Convert auth_mode string to enum if present
    if "auth_mode" in server_data and isinstance(server_data["auth_mode"], str):
        try:
            server_data["auth_mode"] = AuthMode(server_data["auth_mode"])
        except ValueError as e:
            valid_modes = ", ".join(repr(m.value) for m in AuthMode)
            raise ValueError(
                f"Invalid server.auth_mode={server_data['auth_mode']!r}. "
                f"Expected one of: {valid_modes}."
            ) from e

    # Get encryption enabled from config data directly (for test compatibility)
    encryption_enabled = data.get("encryption", {}).get("enabled", False)
    # Get API key hashing enabled from config data directly (under encryption namespace)
    # Default: false - rely on file-level encryption for API key protection
    api_key_hashing_enabled = (
        data.get("encryption", {}).get("api_key_hashing", {}).get("enabled", False)
    )

    # BREAKING CHANGE: Previously, encryption.enabled=true implicitly enabled API key Argon2id hashing.
    # Now, you must explicitly configure encryption.api_key_hashing.enabled=true to enable hashing.
    # When encryption.enabled=true but api_key_hashing.enabled=false (default),
    # API keys will be stored in plaintext within AES-GCM encrypted files.
    if encryption_enabled and not api_key_hashing_enabled:
        logger.info(
            "API key hashing is disabled while file encryption is enabled. "
            "Previously, encryption.enabled=true implicitly enabled API key Argon2id hashing. "
            "Now, API keys will be stored in plaintext within AES-GCM encrypted files. "
            "To maintain the previous behavior, set encryption.api_key_hashing.enabled=true. "
            "See documentation for more details."
        )

    try:
        config = ServerConfig.model_validate(server_data)
    except ValidationError as e:
        raise ValueError(
            f"Invalid server config in {path}:\n"
            f"{format_validation_error(root_model=ServerConfig, error=e, path_prefix='server')}"
        ) from e

    return config.model_copy(
        update={
            "encryption_enabled": encryption_enabled,
            "api_key_hashing_enabled": api_key_hashing_enabled,
        }
    )


_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_localhost(host: str) -> bool:
    """Return True if *host* resolves to a loopback address."""
    return host in _LOCALHOST_HOSTS


def load_bot_gateway_token(config_path: Optional[str] = None) -> str:
    """Load bot gateway token from ov.conf bot.gateway.token."""
    path = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if path is None:
        return ""

    data = load_json_config(path)
    bot_config = data.get("bot", {})
    if not isinstance(bot_config, dict):
        return ""
    gateway_config = bot_config.get("gateway", {})
    if not isinstance(gateway_config, dict):
        return ""
    return gateway_config.get("token", "") or ""


def validate_server_config(config: ServerConfig) -> None:
    """Validate server config for safe startup.

    - **dev mode**: No authentication required, always returns ROOT identity.
      Only acceptable when binding to localhost.
    - **api_key mode**: Authenticates via root_api_key or user keys.
      Requires root_api_key to be configured.
    - **trusted mode**: Trusts X-OpenViking-Account/User/Agent headers.
      Requires root_api_key when binding to non-localhost.

    If auth_mode is not explicitly configured:
    - If root_api_key is configured (non-empty): auto-select API_KEY mode
    - If root_api_key is not configured: auto-select DEV mode

    Raises:
        SystemExit: If the configuration is unsafe.
    """
    # Check for empty root_api_key
    if config.root_api_key == "":
        logger.error(
            "Invalid server.root_api_key: empty string is not allowed. "
            "Either set a non-empty root_api_key or remove the setting entirely."
        )
        sys.exit(1)

    effective_auth_mode = config.get_effective_auth_mode()

    if effective_auth_mode == AuthMode.DEV:
        # Dev mode: no authentication, only allowed on localhost
        if _is_localhost(config.host):
            if config.auth_mode is None:
                logger.warning(
                    "Dev mode (auto-detected): authentication disabled. "
                    "This is allowed because the server is bound to localhost (%s). "
                    "Do NOT expose this server to the network.",
                    config.host,
                )
            else:
                logger.warning(
                    "Dev mode: authentication disabled. This is allowed because the "
                    "server is bound to localhost (%s). Do NOT expose this server "
                    "to the network.",
                    config.host,
                )
            return
        logger.error(
            "SECURITY: server.auth_mode='dev' requires server.host to be localhost, "
            "but it is set to '%s'. Dev mode exposes an unauthenticated ROOT "
            "endpoint and must not be exposed to the network.",
            config.host,
        )
        logger.error(
            "To fix, either:\n"
            '  1. Set server.auth_mode="api_key" and configure server.root_api_key, or\n'
            '  2. Bind dev mode to localhost (server.host = "127.0.0.1")'
        )
        sys.exit(1)

    if effective_auth_mode == AuthMode.TRUSTED:
        if config.root_api_key and config.root_api_key != "":
            return
        if _is_localhost(config.host):
            logger.warning(
                "Trusted mode without API key: authentication trusts "
                "X-OpenViking-Account/User/Agent headers. This is allowed because "
                "the server is bound to localhost (%s).",
                config.host,
            )
            return
        logger.error(
            "SECURITY: server.auth_mode='trusted' requires server.root_api_key when "
            "server.host is '%s' (non-localhost). Only localhost trusted mode may run "
            "without an API key.",
            config.host,
        )
        logger.error(
            "To fix, either:\n"
            "  1. Set server.root_api_key in ov.conf, or\n"
            '  2. Bind trusted mode to localhost (server.host = "127.0.0.1")'
        )
        sys.exit(1)

    # AuthMode.API_KEY
    if config.root_api_key and config.root_api_key != "":
        if config.auth_mode is None:
            logger.info("Api key mode (auto-detected): using root_api_key for authentication")
        return

    # api_key mode without root_api_key is invalid - should use dev mode instead
    if _is_localhost(config.host):
        logger.error(
            "server.auth_mode='api_key' requires server.root_api_key to be configured.\n"
            'To run without authentication on localhost, either set server.auth_mode="dev" '
            "or simply remove the server.auth_mode setting to auto-detect."
        )
    else:
        logger.error(
            "SECURITY: server.auth_mode='api_key' requires server.root_api_key "
            "to be configured when server.host is '%s' (non-localhost).",
            config.host,
        )
    logger.error(
        "To fix, either:\n"
        "  1. Set server.root_api_key in ov.conf, or\n"
        '  2. Use server.auth_mode="dev" (localhost only)'
    )
    sys.exit(1)
