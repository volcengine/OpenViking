# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
from threading import Lock

from pydantic import BaseModel, Field

from .embedding_config import EmbeddingConfig
from .rerank_config import RerankConfig
from .storage_config import StorageConfig
from .vlm_config import VLMConfig
from .parser_config import (
    PDFConfig,
    CodeConfig,
    ImageConfig,
    AudioConfig,
    VideoConfig,
    MarkdownConfig,
    HTMLConfig,
    TextConfig,
    load_parser_configs_from_dict,
)


class OpenVikingConfig(BaseModel):
    """Main configuration for OpenViking."""

    user: Optional[str] = Field(default="default_user", description="Default user identifier")

    storage: StorageConfig = Field(
        default_factory=lambda: StorageConfig(), description="Storage configuration"
    )

    embedding: EmbeddingConfig = Field(
        default_factory=lambda: EmbeddingConfig(), description="Embedding configuration"
    )

    vlm: VLMConfig = Field(default_factory=lambda: VLMConfig(), description="VLM configuration")

    rerank: RerankConfig = Field(
        default_factory=lambda: RerankConfig(), description="Rerank configuration"
    )

    # Parser configurations
    pdf: PDFConfig = Field(
        default_factory=lambda: PDFConfig(), description="PDF parsing configuration"
    )

    code: CodeConfig = Field(
        default_factory=lambda: CodeConfig(), description="Code parsing configuration"
    )

    image: ImageConfig = Field(
        default_factory=lambda: ImageConfig(), description="Image parsing configuration"
    )

    audio: AudioConfig = Field(
        default_factory=lambda: AudioConfig(), description="Audio parsing configuration"
    )

    video: VideoConfig = Field(
        default_factory=lambda: VideoConfig(), description="Video parsing configuration"
    )

    markdown: MarkdownConfig = Field(
        default_factory=lambda: MarkdownConfig(), description="Markdown parsing configuration"
    )

    html: HTMLConfig = Field(
        default_factory=lambda: HTMLConfig(), description="HTML parsing configuration"
    )

    text: TextConfig = Field(
        default_factory=lambda: TextConfig(), description="Text parsing configuration"
    )

    auto_generate_l0: bool = Field(
        default=True, description="Automatically generate L0 (abstract) if not provided"
    )

    auto_generate_l1: bool = Field(
        default=True, description="Automatically generate L1 (overview) if not provided"
    )

    default_search_mode: str = Field(
        default="thinking",
        description="Default search mode: 'fast' (vector only) or 'thinking' (vector + LLM rerank)",
    )

    default_search_limit: int = Field(default=3, description="Default number of results to return")

    enable_memory_decay: bool = Field(default=True, description="Enable automatic memory decay")

    memory_decay_check_interval: int = Field(
        default=3600, description="Interval (seconds) to check for expired memories"
    )

    log_level: str = Field(
        default="WARNING", description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )

    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string",
    )

    log_output: str = Field(
        default="stdout", description="Log output: stdout, stderr, or file path"
    )

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "OpenVikingConfig":
        """Create configuration from dictionary."""
        # Make a copy to avoid modifying the original
        config_copy = config.copy()

        # Handle parser configurations from nested "parsers" section
        parser_configs = {}
        if "parsers" in config_copy:
            parser_configs = config_copy.pop("parsers")

        # Also check for individual parser configs at root level
        parser_types = ["pdf", "code", "image", "audio", "video", "markdown", "html", "text"]
        for parser_type in parser_types:
            if parser_type in config_copy:
                parser_configs[parser_type] = config_copy.pop(parser_type)

        instance = cls(**config_copy)

        # Apply parser configurations
        for parser_type, parser_data in parser_configs.items():
            if hasattr(instance, parser_type):
                config_class = getattr(instance, parser_type).__class__
                setattr(instance, parser_type, config_class.from_dict(parser_data))

        return instance

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()


class OpenVikingConfigSingleton:
    """Global singleton for OpenVikingConfig."""

    _instance: Optional[OpenVikingConfig] = None
    _lock: Lock = Lock()

    @classmethod
    def get_instance(cls) -> OpenVikingConfig:
        """Get the global singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    # Check if environment variable specifies a config file
                    config_file = os.getenv("OPENVIKING_CONFIG_FILE")
                    if config_file and Path(config_file).exists():
                        cls._instance = cls._load_from_file(config_file)
                    else:
                        cls._instance = OpenVikingConfig()
        return cls._instance

    @classmethod
    def initialize(cls, config: Optional[Dict[str, Any]] = None) -> OpenVikingConfig:
        """Initialize the global singleton with optional config dict."""
        with cls._lock:
            if config is None:
                # Check if environment variable specifies a config file
                config_file = os.getenv("OPENVIKING_CONFIG_FILE")
                if config_file and Path(config_file).exists():
                    cls._instance = cls._load_from_file(config_file)
                else:
                    cls._instance = OpenVikingConfig()
            else:
                cls._instance = OpenVikingConfig.from_dict(config)
        return cls._instance

    @classmethod
    def _load_from_file(cls, config_file: str) -> "OpenVikingConfig":
        """Load configuration from JSON config file."""
        try:
            config_path = Path(config_file)
            if not config_path.exists():
                raise FileNotFoundError(f"Config file does not exist: {config_file}")

            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            return OpenVikingConfig.from_dict(config_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Config file JSON format error: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load config file: {e}")

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        with cls._lock:
            cls._instance = None


# Global convenience function
def get_openviking_config() -> OpenVikingConfig:
    """Get the global OpenVikingConfig instance."""
    return OpenVikingConfigSingleton.get_instance()


def set_openviking_config(config: OpenVikingConfig) -> None:
    """Set the global OpenVikingConfig instance."""
    OpenVikingConfigSingleton.initialize(config.to_dict())


def is_valid_openviking_config(config: OpenVikingConfig) -> bool:
    """
    Check if OpenVikingConfig is valid.

    Note: Most validation is now handled by Pydantic validators in individual config classes.
    This function only validates cross-config consistency.

    Raises:
        ValueError: If configuration is invalid with detailed error messages

    Returns:
        bool: True if configuration is valid
    """
    errors = []

    # Validate user identifier
    if not config.user or not config.user.strip():
        errors.append("User identifier cannot be empty")

    # Validate service mode vs embedded mode consistency
    is_service_mode = config.storage.vectordb.backend == "http"
    is_agfs_local = config.storage.agfs.backend == "local"

    if is_service_mode and is_agfs_local and not config.storage.agfs.url:
        errors.append(
            "Service mode (VectorDB backend='http') with local AGFS backend requires 'agfs.url' to be set. "
            "Consider using AGFS backend='s3' or provide remote AGFS URL."
        )

    if errors:
        error_message = "Invalid OpenViking configuration:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_message)

    return True


def initialize_openviking_config(
    config: Optional[OpenVikingConfig] = None,
    user: Optional[str] = None,
    path: Optional[str] = None,
    vectordb_url: Optional[str] = None,
    agfs_url: Optional[str] = None,
) -> OpenVikingConfig:
    """
    Initialize OpenViking configuration with provided parameters.

    Args:
        config: Optional OpenVikingConfig object to use as base configuration
        user: Username for session management
        path: Local storage path for embedded mode
        vectordb_url: Remote VectorDB service URL for service mode
        agfs_url: Remote AGFS service URL for service mode

    Returns:
        Configured OpenVikingConfig instance

    Raises:
        ValueError: If the resulting configuration is invalid
    """
    # Initialize config
    if config is not None:
        set_openviking_config(config)
        config = get_openviking_config()
    else:
        config = get_openviking_config()

    if user:
        # Set user if provided, like a email address or a account_id
        user = user.strip().replace(" ", "_")
        config.user = user

    # Configure storage based on provided parameters
    if path:
        # Embedded mode: local storage
        config.storage.agfs.backend = "local"
        config.storage.agfs.path = path
        config.storage.vectordb.backend = "local"
        config.storage.vectordb.path = path
    elif vectordb_url and agfs_url:
        # Service mode: remote services
        config.storage.agfs.backend = "local"
        config.storage.agfs.url = agfs_url
        config.storage.vectordb.backend = "http"
        config.storage.vectordb.url = vectordb_url

    # Validate configuration
    if not is_valid_openviking_config(config):
        raise ValueError("Invalid OpenViking configuration")

    return config
