# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Unified parser configuration management for OpenViking.

This module consolidates all parser configuration classes that were previously
scattered across different modules. All configurations inherit from ParserConfig
and can be loaded from ov.conf files.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union


@dataclass
class ParserConfig:
    """
    Base configuration class for all parsers.

    This serves as a foundation for parser-specific configurations,
    providing common fields and utilities for all parsers.

    Attributes:
        enabled: Whether the parser is enabled
        max_content_length: Maximum content length to process (characters)
        encoding: Default file encoding
        max_section_size: Maximum characters per section before splitting
        section_size_flexibility: Allow overflow to maintain coherence (0.0-1.0)
    """

    enabled: bool = True
    max_content_length: int = 100000
    encoding: str = "utf-8"

    # Smart splitting configuration
    max_section_size: int = 1000  # Maximum tokens per section before splitting
    section_size_flexibility: float = 0.3  # Allow 30% overflow to maintain coherence

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParserConfig":
        """
        Create configuration from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            ParserConfig instance

        Examples:
            >>> config = ParserConfig.from_dict({"max_content_length": 50000})
        """
        # Filter only fields that belong to this class
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)

    @classmethod
    def from_yaml(cls, yaml_path: Union[str, Path]) -> "ParserConfig":
        """
        Load configuration from YAML file.

        Args:
            yaml_path: Path to YAML configuration file

        Returns:
            ParserConfig instance

        Raises:
            FileNotFoundError: If YAML file doesn't exist
            ValueError: If YAML is invalid

        Examples:
            >>> config = ParserConfig.from_yaml("config.yaml")
        """
        import yaml

        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        if self.max_content_length <= 0:
            raise ValueError("max_content_length must be positive")

        if not self.encoding:
            raise ValueError("encoding cannot be empty")

        if self.max_section_size <= 0:
            raise ValueError("max_section_size must be positive")

        if not 0.0 <= self.section_size_flexibility <= 1.0:
            raise ValueError("section_size_flexibility must be between 0.0 and 1.0")

    def to_dict(self) -> Dict[str, Any]:
        """
        Export configuration as dictionary.

        Returns:
            Configuration dictionary

        Examples:
            >>> config = ParserConfig()
            >>> data = config.to_dict()
        """
        from dataclasses import asdict

        return asdict(self)


@dataclass
class PDFConfig(ParserConfig):
    """
    Configuration for PDF parsing.

    Supports three strategies:
    - "local": Use pdfplumber for local PDF→Markdown conversion
    - "mineru": Use MinerU API for remote PDF→Markdown conversion
    - "auto": Try local first, fallback to MinerU if available

    Attributes:
        strategy: Parsing strategy ("local" | "mineru" | "auto")
        mineru_endpoint: MinerU API endpoint URL
        mineru_api_key: MinerU API authentication key
        mineru_timeout: MinerU request timeout in seconds
        mineruparams: Additional MinerU API parameters
    """

    strategy: str = "auto"  # "local" | "mineru" | "auto"

    # MinerU API configuration
    mineru_endpoint: Optional[str] = None  # API endpoint URL
    mineru_api_key: Optional[str] = None  # API authentication key
    mineru_timeout: float = 300.0  # Request timeout in seconds (5 minutes)
    mineru_params: Optional[dict] = None  # Additional API parameters

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate PDF-specific fields
        if self.strategy not in ("local", "mineru", "auto"):
            raise ValueError(
                f"Invalid strategy '{self.strategy}'. Must be 'local', 'mineru', or 'auto'"
            )

        if self.strategy == "mineru":
            if not self.mineru_endpoint:
                raise ValueError("mineru_endpoint is required when strategy='mineru'")

        if self.mineru_timeout <= 0:
            raise ValueError("mineru_timeout must be positive")


@dataclass
class CodeConfig(ParserConfig):
    """
    Configuration for code parsing.

    Attributes:
        enable_ast: Whether to use AST parsing (for supported languages)
        extract_functions: Whether to extract function definitions
        extract_classes: Whether to extract class definitions
        extract_imports: Whether to extract import statements
        include_comments: Whether to include comments in L1/L2
        max_line_length: Maximum line length before splitting
        language_hint: Optional language hint (auto-detected if None)
        max_token_limit: Maximum tokens to process per file
        truncation_strategy: "head", "tail", or "balanced"
        warn_on_truncation: Whether to warn when truncation occurs
    """

    enable_ast: bool = True
    extract_functions: bool = True
    extract_classes: bool = True
    extract_imports: bool = True
    include_comments: bool = True
    max_line_length: int = 1000
    language_hint: Optional[str] = None
    max_token_limit: int = 50000  # Maximum tokens to process per file
    truncation_strategy: str = "head"  # "head", "tail", or "balanced"
    warn_on_truncation: bool = True

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate code-specific fields
        if self.max_line_length <= 0:
            raise ValueError("max_line_length must be positive")

        if self.max_token_limit <= 0:
            raise ValueError("max_token_limit must be positive")

        if self.truncation_strategy not in ("head", "tail", "balanced"):
            raise ValueError(
                f"Invalid truncation_strategy '{self.truncation_strategy}'. "
                "Must be 'head', 'tail', or 'balanced'"
            )


@dataclass
class ImageConfig(ParserConfig):
    """
    Configuration for image parsing.

    Attributes:
        enable_ocr: Whether to perform OCR text extraction, not implemented
        enable_vlm: Whether to use VLM for visual understanding
        ocr_lang: Language for OCR (e.g., "chi_sim", "eng")
        vlm_model: VLM model to use (e.g., "gpt-4-vision")
        max_dimension: Maximum image dimension (resize if larger)
    """

    enable_ocr: bool = False
    enable_vlm: bool = True
    ocr_lang: str = "eng"
    vlm_model: Optional[str] = None
    max_dimension: int = 2048

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate image-specific fields
        if self.max_dimension <= 0:
            raise ValueError("max_dimension must be positive")


@dataclass
class AudioConfig(ParserConfig):
    """
    Configuration for audio parsing.

    Attributes:
        enable_transcription: Whether to transcribe speech to text
        transcription_model: Model to use (e.g., "whisper-large-v3")
        language: Audio language (None for auto-detection)
        extract_metadata: Whether to extract audio metadata
    """

    enable_transcription: bool = True
    transcription_model: str = "whisper-large-v3"
    language: Optional[str] = None
    extract_metadata: bool = True

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate audio-specific fields
        if not self.transcription_model:
            raise ValueError("transcription_model cannot be empty")


@dataclass
class VideoConfig(ParserConfig):
    """
    Configuration for video parsing.

    Attributes:
        extract_frames: Whether to extract key frames
        frame_interval: Seconds between frame extraction
        enable_transcription: Whether to transcribe audio track
        enable_vlm_description: Whether to use VLM for scene description
        max_duration: Maximum video duration to process (seconds)
    """

    extract_frames: bool = True
    frame_interval: float = 10.0
    enable_transcription: bool = True
    enable_vlm_description: bool = False
    max_duration: float = 3600.0  # 1 hour

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate video-specific fields
        if self.frame_interval <= 0:
            raise ValueError("frame_interval must be positive")

        if self.max_duration <= 0:
            raise ValueError("max_duration must be positive")


@dataclass
class MarkdownConfig(ParserConfig):
    """
    Configuration for Markdown parsing.

    Attributes:
        preserve_links: Whether to preserve hyperlinks in output
        extract_frontmatter: Whether to extract YAML frontmatter
        include_metadata: Whether to include file metadata
        max_heading_depth: Maximum heading depth to include in structure
    """

    preserve_links: bool = True
    extract_frontmatter: bool = True
    include_metadata: bool = True
    max_heading_depth: int = 3

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate markdown-specific fields
        if self.max_heading_depth < 1:
            raise ValueError("max_heading_depth must be at least 1")


@dataclass
class HTMLConfig(ParserConfig):
    """
    Configuration for HTML parsing.

    Attributes:
        extract_text_only: Whether to extract only text content
        preserve_structure: Whether to preserve HTML structure
        clean_html: Whether to clean HTML tags and attributes
        extract_metadata: Whether to extract metadata (title, description)
    """

    extract_text_only: bool = False
    preserve_structure: bool = True
    clean_html: bool = True
    extract_metadata: bool = True

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # No additional validation needed for HTML config


@dataclass
class TextConfig(ParserConfig):
    """
    Configuration for plain text parsing.

    Attributes:
        detect_language: Whether to detect language automatically
        split_by_paragraphs: Whether to split by paragraphs
        max_paragraph_length: Maximum paragraph length before splitting
        preserve_line_breaks: Whether to preserve original line breaks
    """

    detect_language: bool = True
    split_by_paragraphs: bool = True
    max_paragraph_length: int = 1000
    preserve_line_breaks: bool = False

    def validate(self) -> None:
        """
        Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate base class fields
        super().validate()

        # Validate text-specific fields
        if self.max_paragraph_length <= 0:
            raise ValueError("max_paragraph_length must be positive")


# Configuration registry for dynamic loading
PARSER_CONFIG_REGISTRY = {
    "pdf": PDFConfig,
    "code": CodeConfig,
    "image": ImageConfig,
    "audio": AudioConfig,
    "video": VideoConfig,
    "markdown": MarkdownConfig,
    "html": HTMLConfig,
    "text": TextConfig,
}


def get_parser_config(
    parser_type: str, config_data: Optional[Dict[str, Any]] = None
) -> ParserConfig:
    """
    Get parser configuration for a specific parser type.

    Args:
        parser_type: Type of parser (e.g., "pdf", "code", "image")
        config_data: Optional configuration data dictionary

    Returns:
        ParserConfig instance for the specified parser type

    Raises:
        ValueError: If parser_type is not supported

    Examples:
        >>> # Get default PDF configuration
        >>> pdf_config = get_parser_config("pdf")

        >>> # Get custom code configuration
        >>> code_config = get_parser_config("code", {
        ...     "enable_ast": False,
        ...     "max_token_limit": 10000
        ... })
    """
    if parser_type not in PARSER_CONFIG_REGISTRY:
        raise ValueError(
            f"Unsupported parser type: '{parser_type}'. "
            f"Supported types: {list(PARSER_CONFIG_REGISTRY.keys())}"
        )

    config_class = PARSER_CONFIG_REGISTRY[parser_type]

    if config_data:
        return config_class.from_dict(config_data)
    else:
        return config_class()


def load_parser_configs_from_dict(config_dict: Dict[str, Any]) -> Dict[str, ParserConfig]:
    """
    Load all parser configurations from a dictionary.

    Args:
        config_dict: Configuration dictionary with parser sections

    Returns:
        Dictionary mapping parser types to their configurations

    Examples:
        >>> configs = load_parser_configs_from_dict({
        ...     "pdf": {"strategy": "auto"},
        ...     "code": {"enable_ast": false}
        ... })
        >>> pdf_config = configs["pdf"]
        >>> code_config = configs["code"]
    """
    configs = {}

    for parser_type, config_class in PARSER_CONFIG_REGISTRY.items():
        if parser_type in config_dict:
            config_data = config_dict[parser_type]
            configs[parser_type] = config_class.from_dict(config_data)
        else:
            configs[parser_type] = config_class()

    return configs
