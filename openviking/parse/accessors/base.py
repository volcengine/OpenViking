# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Base classes for Data Accessors.

Data Accessors are responsible for fetching data from remote sources
or special paths and making them available as local files/directories.
"""

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Union


class SourceType:
    """
    Enumeration of valid source types for LocalResource.

    Provides type safety and consistency across the system.
    """

    LOCAL = "local"
    """Local file system resource."""

    GIT = "git"
    """Git repository (from GitAccessor)."""

    HTTP = "http"
    """HTTP/HTTPS resource (from HTTPAccessor)."""

    FEISHU = "feishu"
    """Feishu/Lark document (from FeishuAccessor)."""


@dataclass
class LocalResource:
    """
    Represents a locally accessible resource.

    This is the output of the DataAccessor layer, containing the local
    path to the resource along with metadata about its origin.
    """

    path: Path
    """Local file/directory path to the resource."""

    source_type: str
    """Original source type: one of SourceType constants."""

    original_source: str
    """Original source string (URL, path, etc.)."""

    meta: Dict[str, Any] = field(default_factory=dict)
    """Additional metadata (repo_name, branch, content_type, etc.)."""

    is_temporary: bool = True
    """Whether this is a temporary resource that can be cleaned up after parsing."""

    def cleanup(self) -> None:
        """
        Clean up the local resource if it's temporary.

        Removes the file/directory from the local filesystem.
        """
        if not self.is_temporary:
            return

        cleanup_path_value = self.meta.get("_cleanup_path")
        cleanup_path = Path(cleanup_path_value) if cleanup_path_value else self.path

        if not cleanup_path.exists():
            return

        try:
            if cleanup_path.is_dir():
                shutil.rmtree(cleanup_path, ignore_errors=True)
            else:
                cleanup_path.unlink(missing_ok=True)
        except Exception as e:
            from openviking_cli.utils.logger import get_logger

            logger = get_logger(__name__)
            logger.warning(f"[LocalResource] Failed to cleanup resource {cleanup_path}: {e}")

    def __enter__(self) -> "LocalResource":
        """Support context manager protocol."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Support context manager protocol - cleanup on exit."""
        self.cleanup()


class DataAccessor(ABC):
    """
    Abstract base class for data accessors.

    Data Accessors are responsible for:
    - Detecting if they can handle a given source
    - Fetching the data from the source to a local path
    - Providing metadata about the source
    - Cleaning up temporary resources when done
    """

    @abstractmethod
    def can_handle(self, source: Union[str, Path], **kwargs) -> bool:
        """
        Check if this accessor can handle the given source.

        Args:
            source: Source string (URL, path, etc.) or Path object
            **kwargs: Optional accessor-selection hints forwarded from
                ``access()`` (e.g. an explicit ``site=True`` override). Most
                accessors ignore these and decide purely from ``source``.

        Returns:
            True if this accessor can handle the source
        """
        pass

    @abstractmethod
    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Fetch the source and make it available locally.

        Args:
            source: Source string (URL, path, etc.) or Path object
            **kwargs: Additional accessor-specific arguments

        Returns:
            LocalResource pointing to the locally available data
        """
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """
        Priority of this accessor.

        Higher numbers mean higher priority. When multiple accessors
        can handle the same source, the one with the highest priority wins.

        Standard priority levels:
        - 100: Specific service (Feishu, etc.)
        - 80: Version control (Git, etc.)
        - 50: Generic protocols (HTTP, etc.)
        - 10: Fallback accessors
        """
        pass

    def cleanup(self, resource: LocalResource) -> None:
        """
        Clean up the local resource.

        Default implementation calls resource.cleanup().
        Subclasses can override for custom cleanup logic.

        Args:
            resource: The LocalResource to clean up
        """
        resource.cleanup()
