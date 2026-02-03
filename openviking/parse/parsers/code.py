# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Code Repository Parser for OpenViking.

Implements the CodeRepositoryParser for parsing entire code repositories
with 1:1 directory structure mapping.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Union
from urllib.parse import urlparse

from openviking.parse.base import ParseResult, ResourceNode, NodeType, create_parse_result
from openviking.parse.parsers.base_parser import BaseParser
from openviking.utils.logger import get_logger

logger = get_logger(__name__)


class CodeRepositoryParser(BaseParser):
    """
    Code Repository Parser for handling entire code repositories.

    Features:
    1. Repository URL detection (GitHub/GitLab)
    2. Git clone or zip download with shallow cloning
    3. Automatic filtering of non-code resources
    4. 1:1 directory structure mapping
    5. File-level granularity (no chunking)
    6. Async processing with semantic generation in background

    Supported repository URLs:
    - GitHub: https://github.com/org/repo
    - GitLab: https://gitlab.com/org/repo
    - Git URLs: https://..., git@..., *.git

    Design Principles:
    1. File granularity: Keep single files intact (no splitting)
    2. Structure preservation: Maintain exact directory hierarchy
    3. Async processing: Fast physical copy, slow semantic generation
    4. Filtering: Ignore .git, node_modules, __pycache__, etc.
    """

    # Repository URL patterns
    _REPO_PATTERNS = [
        r"^https?://github\.com/[^/]+/[^/]+/?$",
        r"^https?://gitlab\.com/[^/]+/[^/]+/?$",
        r"^.*\.git$",
        r"^git@",
    ]

    # Default ignore patterns for non-code resources
    _IGNORE_PATTERNS = [
        ".git",
        ".idea",
        ".vscode",
        "__pycache__",
        "node_modules",
        ".next",
        ".nuxt",
        "dist",
        "build",
        "target",
        ".gradle",
        ".mvn",
        "venv",
        ".venv",
        "env",
        ".env",
        "*.pyc",
        "*.pyo",
        "*.pyd",
        "*.so",
        "*.dll",
        "*.exe",
        "*.bin",
        "*.jar",
        "*.war",
        "*.tar",
        "*.gz",
        "*.zip",
        "*.rar",
        "*.7z",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.webp",
        "*.mp4",
        "*.mov",
        "*.avi",
        "*.webm",
        "*.mp3",
        "*.wav",
        "*.m4a",
        "*.flac",
    ]

    # Supported file extensions for code files
    _CODE_EXTENSIONS = [
        # Python
        ".py",
        # C/C++
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",
        # Java
        ".java",
        # JavaScript/TypeScript
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        # Go
        ".go",
        # Rust
        ".rs",
        # PHP
        ".php",
        # Ruby
        ".rb",
        # Swift
        ".swift",
        # Kotlin
        ".kt",
        ".kts",
        # Scala
        ".scala",
        # C#
        ".cs",
        # F#
        ".fs",
        # VB.NET
        ".vb",
        # Perl
        ".pl",
        ".pm",
        # Lua
        ".lua",
        # Haskell
        ".hs",
        # Erlang
        ".erl",
        # Elixir
        ".ex",
        ".exs",
        # Clojure
        ".clj",
        ".cljs",
        # Dart
        ".dart",
        # Shell
        ".sh",
        ".bash",
        # SQL
        ".sql",
        # Configuration
        ".yaml",
        ".yml",
        ".json",
        ".xml",
        ".ini",
        ".cfg",
        ".conf",
        # Web
        ".html",
        ".css",
        ".scss",
        ".less",
        # Documentation
        ".md",
        ".markdown",
        ".rst",
        ".txt",
        # Other
        ".dockerfile",
        "dockerfile",
        ".makefile",
        "makefile",
    ]

    def __init__(self, **kwargs):
        """
        Initialize code repository parser.

        Args:
            **kwargs: Additional configuration parameters
        """
        super().__init__(**kwargs)

    @property
    def supported_extensions(self) -> List[str]:
        """
        Return supported file extensions.

        Note: This parser primarily handles repository URLs,
        but also supports individual code files as fallback.
        """
        return self._CODE_EXTENSIONS

    def is_repository_url(self, source: str) -> bool:
        """
        Check if the source is a repository URL.

        Args:
            source: Source string (URL or path)

        Returns:
            True if source matches repository URL patterns
        """
        import re

        source_str = str(source)

        # Check for URL patterns
        for pattern in self._REPO_PATTERNS:
            if re.match(pattern, source_str):
                return True

        # Check if it's a GitHub/GitLab URL
        parsed = urlparse(source_str)
        if parsed.netloc in ["github.com", "gitlab.com"]:
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                return True

        return False

    async def _clone_repository(self, repo_url: str, target_dir: Path) -> bool:
        """
        Clone repository using git.

        Args:
            repo_url: Repository URL
            target_dir: Target directory for clone

        Returns:
            True if successful, False otherwise
        """
        try:
            # Use shallow clone for speed
            cmd = f"git clone --depth 1 {repo_url} {target_dir}"
            logger.info(f"Cloning repository: {cmd}")

            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info(f"Successfully cloned repository to {target_dir}")
                return True
            else:
                logger.error(f"Git clone failed: {stderr.decode()}")
                return False

        except Exception as e:
            logger.error(f"Git clone error: {e}")
            return False

    async def _download_repository_zip(self, repo_url: str, target_dir: Path) -> bool:
        """
        Download repository as zip file.

        Args:
            repo_url: Repository URL
            target_dir: Target directory for extraction

        Returns:
            True if successful, False otherwise
        """
        import zipfile
        import io

        # Convert GitHub URL to zip download URL
        if "github.com" in repo_url:
            # Remove .git suffix if present
            if repo_url.endswith(".git"):
                repo_url = repo_url[:-4]

            # Add /archive/refs/heads/main.zip
            if not repo_url.endswith("/"):
                repo_url += "/"
            zip_url = repo_url + "archive/refs/heads/main.zip"
        else:
            # For other repos, try to download as zip
            zip_url = repo_url
            if repo_url.endswith(".git"):
                zip_url = repo_url[:-4] + "/archive/main.zip"

        logger.info(f"Downloading repository zip: {zip_url}")

        # Try aiohttp first
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(zip_url) as response:
                    if response.status == 200:
                        content = await response.read()
                        with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
                            zip_file.extractall(target_dir)
                        logger.info(
                            f"Successfully downloaded and extracted repository to {target_dir}"
                        )
                        return True
                    else:
                        logger.error(f"Failed to download zip: HTTP {response.status}")
                        return False
        except ImportError:
            # Fallback to requests
            logger.warning("aiohttp not available, falling back to requests")
            try:
                import requests

                response = requests.get(zip_url)
                if response.status_code == 200:
                    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
                        zip_file.extractall(target_dir)
                    logger.info(f"Successfully downloaded and extracted repository to {target_dir}")
                    return True
                else:
                    logger.error(f"Failed to download zip: HTTP {response.status_code}")
                    return False
            except ImportError:
                logger.error("Neither aiohttp nor requests available for zip download")
                return False
            except Exception as e:
                logger.error(f"Requests download error: {e}")
                return False
        except Exception as e:
            logger.error(f"aiohttp download error: {e}")
            return False

    async def _download_with_aiohttp(self, zip_url: str, target_dir: Path) -> bool:
        """Download repository zip using aiohttp."""
        import aiohttp
        import zipfile
        import io

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(zip_url) as response:
                    if response.status == 200:
                        content = await response.read()

                        # Extract zip to target directory
                        with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
                            zip_file.extractall(target_dir)

                        logger.info(
                            f"Successfully downloaded and extracted repository to {target_dir}"
                        )
                        return True
                    else:
                        logger.error(f"Failed to download zip: HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"aiohttp download error: {e}")
            return False

    def _download_with_requests(self, zip_url: str, target_dir: Path) -> bool:
        """Download repository zip using requests (synchronous)."""
        import requests
        import zipfile
        import io

        try:
            response = requests.get(zip_url)
            if response.status_code == 200:
                content = response.content

                # Extract zip to target directory
                with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
                    zip_file.extractall(target_dir)

                logger.info(f"Successfully downloaded and extracted repository to {target_dir}")
                return True
            else:
                logger.error(f"Failed to download zip: HTTP {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Requests download error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during zip download: {e}")
            return False

        except ImportError:
            # Fallback using requests
            logger.warning("aiohttp not available, falling back to requests")
            try:
                import requests

                response = requests.get(zip_url)
                if response.status_code == 200:
                    content = response.content

                    # Extract zip to target directory
                    with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
                        zip_file.extractall(target_dir)

                    logger.info(f"Successfully downloaded and extracted repository to {target_dir}")
                    return True
                else:
                    logger.error(f"Failed to download zip: HTTP {response.status_code}")
                    return False

            except requests.exceptions.RequestException as e:
                logger.error(f"Zip download error: {e}")
                return False
            except Exception as e:
                logger.error(f"Unexpected error during zip download: {e}")
                return False
            else:
                # Fallback using requests
                response = requests.get(zip_url)
                if response.status_code == 200:
                    content = response.content

                    # Extract zip to target directory
                    with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
                        zip_file.extractall(target_dir)

                    logger.info(f"Successfully downloaded and extracted repository to {target_dir}")
                    return True
                else:
                    logger.error(f"Failed to download zip: HTTP {response.status_code}")
                    return False

        except Exception as e:
            logger.error(f"Zip download error: {e}")
            return False

        except Exception as e:
            logger.error(f"Zip download error: {e}")
            return False

    def _should_ignore(self, path: Path) -> bool:
        """
        Check if a path should be ignored based on patterns.

        Args:
            path: Path to check

        Returns:
            True if path should be ignored
        """
        path_str = str(path)

        # Check ignore patterns
        for pattern in self._IGNORE_PATTERNS:
            if pattern.startswith("*."):
                # File extension pattern
                if path_str.endswith(pattern[1:]):
                    return True
            elif pattern in path_str:
                # Directory or file name pattern
                return True

        return False

    def _is_code_file(self, path: Path) -> bool:
        """
        Check if a file is a code file based on extension.

        Args:
            path: File path

        Returns:
            True if file has a code extension
        """
        return path.suffix.lower() in self._CODE_EXTENSIONS

    async def _copy_filtered_repository(self, source_dir: Path, temp_dir: Path) -> None:
        """
        Copy repository with filtering.

        Args:
            source_dir: Source repository directory
            temp_dir: Temporary directory for filtered copy
        """
        # Walk through source directory
        for root, dirs, files in os.walk(source_dir):
            root_path = Path(root)

            # Filter directories to skip
            dirs[:] = [d for d in dirs if not self._should_ignore(root_path / d)]

            # Process files
            for file in files:
                file_path = root_path / file

                # Skip ignored files
                if self._should_ignore(file_path):
                    continue

                # Skip non-code files (except documentation)
                if not self._is_code_file(file_path) and file_path.suffix.lower() not in [
                    ".md",
                    ".markdown",
                    ".txt",
                    ".rst",
                ]:
                    continue

                # Calculate relative path
                rel_path = file_path.relative_to(source_dir)
                target_path = temp_dir / rel_path

                # Create target directory if needed
                target_path.parent.mkdir(parents=True, exist_ok=True)

                # Copy file
                shutil.copy2(file_path, target_path)
                logger.debug(f"Copied: {rel_path}")

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse code repository or file.

        Phase 1: Repository detection and download
        Phase 2: Filtering and copying to temp directory
        Phase 3: Create ResourceNode structure

        Args:
            source: Repository URL or file path
            instruction: Optional parsing instruction
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with repository structure

        Raises:
            ValueError: If source cannot be processed
        """
        source_str = str(source)

        # Check if it's a repository URL
        if self.is_repository_url(source_str):
            logger.info(f"Processing repository URL: {source_str}")
            return await self._parse_repository(source_str, instruction, **kwargs)
        else:
            # Fall back to single file parsing
            logger.info(f"Processing code file: {source_str}")
            return await self._parse_code_file(source_str, instruction, **kwargs)

    async def _parse_repository(
        self, repo_url: str, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse a code repository.

        Args:
            repo_url: Repository URL
            instruction: Optional parsing instruction
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with repository structure
        """
        # Create temporary directories
        with (
            tempfile.TemporaryDirectory() as download_dir,
            tempfile.TemporaryDirectory() as temp_dir,
        ):
            download_path = Path(download_dir)
            temp_path = Path(temp_dir)

            # Try git clone first
            success = await self._clone_repository(repo_url, download_path)

            # Fall back to zip download if git clone fails
            if not success:
                success = await self._download_repository_zip(repo_url, download_path)

            if not success:
                raise ValueError(f"Failed to download repository: {repo_url}")

            # Copy with filtering
            await self._copy_filtered_repository(download_path, temp_path)

            # Create root ResourceNode
            repo_name = self._extract_repository_name(repo_url)
            root = ResourceNode(type=NodeType.ROOT, title=repo_name)
            # Store repository URL in metadata
            root.meta["description"] = f"Code repository: {repo_url}"
            root.meta["repository_url"] = repo_url

            # Create ParseResult
            result = create_parse_result(
                root=root,
                source_path=repo_url,
                source_format="code_repository",
                parser_name="CodeRepositoryParser",
            )
            # Store temp directory path in metadata
            result.meta["temp_dir_path"] = str(temp_path)

            logger.info(f"Successfully parsed repository: {repo_url}")
            return result

    async def _parse_code_file(
        self, file_path: str, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse a single code file (fallback).

        Args:
            file_path: Code file path
            instruction: Optional parsing instruction
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with file content
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"Code file not found: {file_path}")

        # Read file content
        content = path.read_text(encoding="utf-8", errors="ignore")

        # Create root ResourceNode
        root = ResourceNode(type=NodeType.ROOT, title=path.name)
        # Store file info in metadata
        root.meta["description"] = f"Code file: {path.name}"
        root.meta["content_preview"] = content[:500] + "..." if len(content) > 500 else content

        # Create ParseResult
        result = create_parse_result(
            root=root,
            source_path=str(path),
            source_format="code_file",
            parser_name="CodeRepositoryParser",
        )

        logger.info(f"Successfully parsed code file: {file_path}")
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse code from content string.

        Note: This parser primarily handles files and URLs.
        For content strings, we create a temporary file.

        Args:
            content: Code content string
            source_path: Optional source path for metadata
            instruction: Optional parsing instruction
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with code content
        """
        if not content:
            raise ValueError("Code content cannot be empty")

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(content)
            temp_file = f.name

        try:
            # Parse the temporary file
            return await self.parse(temp_file, instruction, **kwargs)
        finally:
            # Clean up
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def _extract_repository_name(self, repo_url: str) -> str:
        """
        Extract repository name from URL.

        Args:
            repo_url: Repository URL

        Returns:
            Repository name
        """
        parsed = urlparse(repo_url)

        if parsed.netloc == "github.com":
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                return path_parts[1]

        elif parsed.netloc == "gitlab.com":
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                return path_parts[1]

        # Fallback: use last part of path
        path_parts = parsed.path.strip("/").split("/")
        if path_parts:
            name = path_parts[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name

        return "unknown_repository"
