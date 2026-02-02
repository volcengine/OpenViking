# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Code file parser for OpenViking.

Supports multiple programming languages with syntax-aware parsing.
"""

from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking.utils.config.parser_config import CodeConfig
from openviking.utils.logger import get_logger

logger = get_logger(__name__)


class CodeParser(BaseParser):
    """
    Code file parser for multiple programming languages.

    Features:
    1. Language detection based on file extension and content
    2. AST parsing for supported languages (Python, JavaScript/TypeScript via tree-sitter)
    3. Syntax-aware chunking for better context retrieval
    4. Semantic structure extraction (functions, classes, imports)
    5. Integration with OpenViking's L0/L1/L2 model

    Supported languages:
    - Python (.py)
    - C/C++ (.c, .cpp, .cc, .h, .hpp)
    - Java (.java)
    - JavaScript/TypeScript (.js, .jsx, .ts, .tsx)
    - Go (.go)
    - Rust (.rs)
    - PHP (.php)
    - Shell (.sh, .bash)
    - Ruby (.rb)
    - Swift (.swift)
    - Kotlin (.kt)
    - Scala (.scala)
    - R (.r)
    - Julia (.jl)
    """

    # Language detection mapping
    _EXTENSION_MAP = {
        # Python
        ".py": "python",
        # C/C++
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        # Java
        ".java": "java",
        # JavaScript/TypeScript
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        # Go
        ".go": "go",
        # Rust
        ".rs": "rust",
        # PHP
        ".php": "php",
        # Shell
        ".sh": "shell",
        ".bash": "shell",
        # Ruby
        ".rb": "ruby",
        # Swift
        ".swift": "swift",
        # Kotlin
        ".kt": "kotlin",
        ".kts": "kotlin",
        # Scala
        ".scala": "scala",
        # R
        ".r": "r",
        ".R": "r",
        # Julia
        ".jl": "julia",
        # Other
        ".cs": "csharp",  # C#
        ".fs": "fsharp",  # F#
        ".vb": "vbnet",  # VB.NET
        ".pl": "perl",  # Perl
        ".pm": "perl",  # Perl module
        ".lua": "lua",
        ".hs": "haskell",  # Haskell
        ".erl": "erlang",  # Erlang
        ".ex": "elixir",  # Elixir
        ".exs": "elixir",  # Elixir script
        ".clj": "clojure",  # Clojure
        ".cljs": "clojurescript",  # ClojureScript
        ".dart": "dart",
        ".sql": "sql",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
    }

    def __init__(self, config: Optional[CodeConfig] = None, **kwargs):
        """
        Initialize code parser.

        Args:
            config: Code parsing configuration
            **kwargs: Additional configuration parameters
        """
        self.config = config or CodeConfig()

    @property
    def supported_extensions(self) -> List[str]:
        """Return supported code file extensions."""
        return list(self._EXTENSION_MAP.keys())

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse code file using three-phase architecture.

        Phase 1: Language detection and content reading
        Phase 2: Syntax-aware analysis and structure extraction
        Phase 3: Generate L0/L1/L2 content and create ResourceNode

        Args:
            source: Code file path
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with code content structured for retrieval

        Raises:
            FileNotFoundError: If source file does not exist
            ValueError: If language cannot be detected
        """
        pass

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse code from content string.

        Note: CodeParser primarily handles file paths. For content strings,
        a temporary file is created for processing.

        Args:
            content: Code content string
            source_path: Optional source path for metadata
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with code content

        Raises:
            ValueError: If content is empty
        """
        pass
