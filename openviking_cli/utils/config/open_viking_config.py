# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from openviking_cli.session.user_id import UserIdentifier

from .config_loader import resolve_config_path
from .config_utils import format_validation_error, raise_unknown_config_fields
from .consts import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_OV_CONF,
    OPENVIKING_CONFIG_ENV,
    SYSTEM_CONFIG_DIR,
)
from .embedding_config import EmbeddingConfig
from .encryption_config import EncryptionConfig
from .log_config import LogConfig
from .memory_config import MemoryConfig
from .parser_config import (
    AudioConfig,
    CodeConfig,
    DirectoryConfig,
    FeishuConfig,
    HTMLConfig,
    ImageConfig,
    MarkdownConfig,
    PDFConfig,
    SemanticConfig,
    TextConfig,
    VideoConfig,
)
from .prompts_config import PromptsConfig
from .rerank_config import RerankConfig
from .retrieval_config import RetrievalConfig
from .storage_config import StorageConfig
from .telemetry_config import TelemetryConfig
from .vlm_config import VLMConfig


class OpenVikingConfig(BaseModel):
    """Main configuration for OpenViking."""

    default_account: Optional[str] = Field(
        default="default", description="Default account identifier"
    )
    default_user: Optional[str] = Field(default="default", description="Default user identifier")
    default_agent: Optional[str] = Field(default="default", description="Default agent identifier")

    storage: StorageConfig = Field(
        default_factory=StorageConfig, description="Storage configuration"
    )

    embedding: EmbeddingConfig = Field(
        default_factory=EmbeddingConfig, description="Embedding configuration"
    )

    embeddings: Dict[str, EmbeddingConfig] = Field(
        default_factory=dict,
        description="Named embedding configurations for migration (e.g. {'default': ..., 'v2': ...})",
    )

    vlm: VLMConfig = Field(default_factory=VLMConfig, description="VLM configuration")

    rerank: RerankConfig = Field(default_factory=RerankConfig, description="Rerank configuration")

    retrieval: RetrievalConfig = Field(
        default_factory=RetrievalConfig,
        description="Retrieval ranking configuration",
    )

    # Encryption configuration
    encryption: EncryptionConfig = Field(
        default_factory=EncryptionConfig, description="Encryption configuration"
    )

    # Parser configurations
    pdf: PDFConfig = Field(default_factory=PDFConfig, description="PDF parsing configuration")

    code: CodeConfig = Field(default_factory=CodeConfig, description="Code parsing configuration")

    image: ImageConfig = Field(
        default_factory=ImageConfig, description="Image parsing configuration"
    )

    audio: AudioConfig = Field(
        default_factory=AudioConfig, description="Audio parsing configuration"
    )

    video: VideoConfig = Field(
        default_factory=VideoConfig, description="Video parsing configuration"
    )

    markdown: MarkdownConfig = Field(
        default_factory=MarkdownConfig, description="Markdown parsing configuration"
    )

    html: HTMLConfig = Field(default_factory=HTMLConfig, description="HTML parsing configuration")

    text: TextConfig = Field(default_factory=TextConfig, description="Text parsing configuration")

    directory: DirectoryConfig = Field(
        default_factory=DirectoryConfig, description="Directory parsing configuration"
    )

    feishu: FeishuConfig = Field(
        default_factory=FeishuConfig,
        description="Feishu/Lark document parsing configuration",
    )

    semantic: SemanticConfig = Field(
        default_factory=SemanticConfig,
        description="Semantic processing configuration (overview/abstract limits)",
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

    language_fallback: str = Field(
        default="en",
        description=(
            "Deprecated. No longer used — detection falls back to 'en' when no language can be "
            "inferred. Set output_language_override instead to pin an explicit language."
        ),
    )

    output_language_override: str = Field(
        default="",
        description=(
            "When non-empty, bypasses content-based language detection for memory extraction "
            "and semantic summaries/overviews and forces this language instead. Use when your "
            "corpus is mixed-language but you want summaries pinned to a single language "
            "(e.g., 'en', 'zh-CN', 'ja'). Leave empty (default) to auto-detect per content."
        ),
    )

    @model_validator(mode="after")
    def _warn_on_deprecated_language_fallback(self) -> "OpenVikingConfig":
        if self.language_fallback and self.language_fallback != "en":
            logging.getLogger(__name__).warning(
                "Config field 'language_fallback=%s' is deprecated and has no effect; "
                "remove it, or set 'output_language_override' to pin an explicit language.",
                self.language_fallback,
            )
        return self

    @model_validator(mode="after")
    def _resolve_embedding_from_embeddings(self) -> "OpenVikingConfig":
        """Resolve active embedding from the embeddings map.

        When embeddings is non-empty:
        1. Read migration state file for current_active
        2. Auto-create state file with "default" if missing
        3. Set self.embedding = self.embeddings[current_active]
        4. Reject on dimension mismatch with storage.vectordb

        When embeddings is empty: no-op (backward compat).
        """
        if not self.embeddings:
            return self  # backward compat — no migration configs present

        # Determine config directory for the migration state file.
        # Respect OPENVIKING_CONFIG_DIR for testability; fall back to the
        # standard ~/.openviking directory.
        config_dir_str = os.environ.get("OPENVIKING_CONFIG_DIR")
        if config_dir_str:
            config_dir = Path(config_dir_str)
        else:
            config_dir = DEFAULT_CONFIG_DIR

        state_file_path = config_dir / "embedding_migration_state.json"

        if state_file_path.exists():
            try:
                state = json.loads(state_file_path.read_text(encoding="utf-8"))
                current_active = state["current_active"]
            except (json.JSONDecodeError, KeyError) as e:
                raise ValueError(
                    f"Embedding migration state file at {state_file_path} is corrupt: {e}"
                ) from e
        else:
            # No state file — must have "default" key in embeddings.
            # User's original embedding config becomes embeddings["default"].
            # If no "default" key, error out — the user must create it.
            if "default" not in self.embeddings:
                raise ValueError(
                    "embeddings configured but no 'default' key found and no "
                    "embedding_migration_state.json exists. "
                    "Add 'default' to embeddings pointing to the current embedding config."
                )
            current_active = "default"
            # Auto-create state file
            config_dir.mkdir(parents=True, exist_ok=True)
            initial_state = {
                "version": 1,
                "current_active": current_active,
                "history": [],
            }
            state_file_path.write_text(
                json.dumps(initial_state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Validate current_active exists in embeddings
        if current_active not in self.embeddings:
            raise ValueError(
                f"current_active '{current_active}' not found in embeddings. "
                f"Available keys: {list(self.embeddings.keys())}"
            )

        # Resolve the active embedding config
        self.embedding = self.embeddings[current_active]

        # Dimension consistency check with storage
        if (
            self.storage is not None
            and self.storage.vectordb is not None
            and self.embedding is not None
        ):
            emb_dim = self.embedding.dimension
            store_dim = self.storage.vectordb.dimension
            if emb_dim > 0 and store_dim > 0 and emb_dim != store_dim:
                raise ValueError(
                    f"Dimension mismatch: embedding.dimension={emb_dim}, "
                    f"storage.vectordb.dimension={store_dim}"
                )

        return self

    allow_private_networks: bool = Field(
        default=False,
        description=(
            "Allow fetching resources from private/non-public network addresses. "
            "When disabled (default), only public IP addresses and hostnames are allowed."
        ),
    )

    log: LogConfig = Field(default_factory=LogConfig, description="Logging configuration")

    memory: MemoryConfig = Field(default_factory=MemoryConfig, description="Memory configuration")

    telemetry: "TelemetryConfig" = Field(
        default_factory=TelemetryConfig, description="Telemetry configuration"
    )
    prompts: PromptsConfig = Field(
        default_factory=PromptsConfig,
        description="Prompt template configuration",
    )

    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "OpenVikingConfig":
        """Create configuration from dictionary."""
        try:
            # Make a copy to avoid modifying the original
            config_copy = config.copy()

            parser_types = [
                "pdf",
                "code",
                "image",
                "audio",
                "video",
                "markdown",
                "html",
                "text",
                "directory",
                "feishu",
            ]
            raise_unknown_config_fields(
                data=config_copy,
                valid_fields=set(cls.model_fields.keys()) | {"server", "bot", "parsers"},
                context_name="OpenVikingConfig",
            )

            # Remove sections managed by other loaders (e.g. server config)
            config_copy.pop("server", None)
            config_copy.pop("bot", None)

            # Handle parser configurations from nested "parsers" section
            parser_configs = {}
            if "parsers" in config_copy:
                parser_configs = config_copy.pop("parsers")
                if parser_configs is None:
                    parser_configs = {}
                if not isinstance(parser_configs, dict):
                    raise ValueError("Invalid parsers config: 'parsers' section must be an object")
            raise_unknown_config_fields(
                data=parser_configs,
                valid_fields=set(parser_types),
                context_name="parsers",
            )
            for parser_type in parser_types:
                if parser_type in config_copy:
                    parser_configs[parser_type] = config_copy.pop(parser_type)

            # Handle log configuration from nested "log" section
            log_config_data = None
            if "log" in config_copy:
                log_config_data = config_copy.pop("log")

            # Handle memory configuration from nested "memory" section
            memory_config_data = None
            if "memory" in config_copy:
                memory_config_data = config_copy.pop("memory")

            instance = cls(**config_copy)

            # Apply log configuration
            if log_config_data is not None:
                instance.log = LogConfig.from_dict(log_config_data)

            # Apply memory configuration
            if memory_config_data is not None:
                if (
                    isinstance(memory_config_data, dict)
                    and "agent_scope_mode" in memory_config_data
                ):
                    logging.getLogger(__name__).warning(
                        "memory.agent_scope_mode is deprecated and ignored. "
                        "User/agent namespace behavior is now controlled by per-account "
                        "namespace policy."
                    )
                instance.memory = MemoryConfig.from_dict(memory_config_data)

            # Apply parser configurations
            for parser_type, parser_data in parser_configs.items():
                if hasattr(instance, parser_type):
                    config_class = getattr(instance, parser_type).__class__
                    setattr(instance, parser_type, config_class.from_dict(parser_data))

            # Check dimension consistency
            if (
                getattr(instance, "storage", None)
                and getattr(instance.storage, "vectordb", None)
                and getattr(instance, "embedding", None)
            ):
                db_dim = instance.storage.vectordb.dimension
                emb_dim = instance.embedding.dimension
                if db_dim > 0 and emb_dim > 0 and db_dim != emb_dim:
                    logging.warning(
                        f"Dimension mismatch: VectorDB dimension is {db_dim}, "
                        f"but Embedding dimension is {emb_dim}. "
                        "This may cause errors during vector search."
                    )
            return instance
        except ValidationError as e:
            raise ValueError(format_validation_error(root_model=cls, error=e)) from e

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()


class OpenVikingConfigSingleton:
    """Global singleton for OpenVikingConfig.

    Resolution chain for ov.conf:
      1. Explicit path passed to initialize()
      2. OPENVIKING_CONFIG_FILE environment variable
      3. ~/.openviking/ov.conf
      4. /etc/openviking/ov.conf
      5. Error with clear guidance

    ``_initializing`` prevents a same-thread deadlock: loading the config
    triggers pydantic validation which can import modules whose module-level
    ``get_logger()`` calls ``get_instance()`` again *before* the lock is
    released.  The flag is checked **before** ``_lock.acquire()`` so the
    re-entrant call raises immediately, letting ``_load_log_config()``
    fall back to default logging.
    """

    _instance: Optional[OpenVikingConfig] = None
    _lock: Lock = Lock()
    _initializing: bool = False

    @classmethod
    def get_instance(cls) -> OpenVikingConfig:
        """Get the global singleton instance.

        Raises FileNotFoundError if no config file is found.
        Raises RuntimeError if called re-entrantly during initialization.
        """
        if cls._initializing:
            raise RuntimeError(
                "OpenVikingConfigSingleton is still initializing "
                "(re-entrant call detected, falling back to defaults)"
            )
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._initializing = True
                    try:
                        config_path = resolve_config_path(
                            None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF
                        )
                        if config_path is not None:
                            cls._instance = cls._load_from_file(str(config_path))
                        else:
                            default_path_user = DEFAULT_CONFIG_DIR / DEFAULT_OV_CONF
                            default_path_system = SYSTEM_CONFIG_DIR / DEFAULT_OV_CONF
                            raise FileNotFoundError(
                                f"OpenViking configuration file not found.\n"
                                f"Please create {default_path_user} or {default_path_system}, "
                                f"or set {OPENVIKING_CONFIG_ENV}.\n"
                                f"See: https://openviking.ai/docs"
                            )
                    finally:
                        cls._initializing = False
        return cls._instance

    @classmethod
    def initialize(
        cls,
        config_dict: Optional[Dict[str, Any]] = None,
        config_path: Optional[str] = None,
    ) -> OpenVikingConfig:
        """Initialize the global singleton.

        Args:
            config_dict: Direct config dictionary (highest priority).
            config_path: Explicit path to ov.conf file.
        """
        with cls._lock:
            cls._initializing = True
            try:
                if config_dict is not None:
                    cls._instance = OpenVikingConfig.from_dict(config_dict)
                else:
                    path = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
                    if path is not None:
                        cls._instance = cls._load_from_file(str(path))
                    else:
                        default_path_user = DEFAULT_CONFIG_DIR / DEFAULT_OV_CONF
                        default_path_system = SYSTEM_CONFIG_DIR / DEFAULT_OV_CONF
                        raise FileNotFoundError(
                            f"OpenViking configuration file not found.\n"
                            f"Please create {default_path_user} or {default_path_system}, "
                            f"or set {OPENVIKING_CONFIG_ENV}.\n"
                            f"See: https://openviking.ai/docs"
                        )
            finally:
                cls._initializing = False
        return cls._instance

    @classmethod
    def _load_from_file(cls, config_file: str) -> "OpenVikingConfig":
        """Load configuration from JSON config file."""
        try:
            config_path = Path(config_file)
            if not config_path.exists():
                raise FileNotFoundError(f"Config file does not exist: {config_file}")

            with open(config_path, "r", encoding="utf-8") as f:
                raw = f.read()

            # Expand $VAR and ${VAR} inside the JSON text (useful for container deployments).
            # Unset variables are left unchanged by expandvars().
            raw = os.path.expandvars(raw)
            config_data = json.loads(raw)

            return OpenVikingConfig.from_dict(config_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Config file JSON format error: {e}")
        except ValueError:
            raise
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
    OpenVikingConfigSingleton.initialize(config_dict=config.to_dict())


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

    # Validate account identifier
    if not config.default_account or not config.default_account.strip():
        errors.append("Default account identifier cannot be empty")

    if errors:
        error_message = "Invalid OpenViking configuration:\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        raise ValueError(error_message)

    return True


def initialize_openviking_config(
    user: Optional[UserIdentifier] = None,
    path: Optional[str] = None,
) -> OpenVikingConfig:
    """
    Initialize OpenViking configuration with provided parameters.

    Loads ov.conf from the standard resolution chain, then applies
    parameter overrides.

    Args:
        user: UserIdentifier for session management
        path: Local storage path (workspace) for embedded mode

    Returns:
        Configured OpenVikingConfig instance

    Raises:
        ValueError: If the resulting configuration is invalid
        FileNotFoundError: If no config file is found
    """
    config = get_openviking_config()

    if user:
        # Set user if provided, like a email address or a account_id
        config.default_account = user._account_id
        config.default_user = user._user_id
        config.default_agent = user._agent_id

    # Configure storage based on provided parameters
    if path:
        # Embedded mode: local storage
        config.storage.agfs.backend = config.storage.agfs.backend or "local"
        config.storage.vectordb.backend = config.storage.vectordb.backend or "local"
        # Resolve and update workspace + dependent paths (model_validator won't
        # re-run on attribute assignment, so sync agfs.path / vectordb.path here).
        workspace_path = Path(path).expanduser().resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        resolved = str(workspace_path)
        config.storage.workspace = resolved
        config.storage.agfs.path = resolved
        config.storage.vectordb.path = resolved

    # Ensure vector dimension is synced if not set in storage
    if config.storage.vectordb.dimension == 0:
        config.storage.vectordb.dimension = config.embedding.dimension

    # Validate configuration
    if not is_valid_openviking_config(config):
        raise ValueError("Invalid OpenViking configuration")

    return config
