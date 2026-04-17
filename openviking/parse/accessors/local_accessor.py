# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Local file system accessor for OpenViking.

Provides a DataAccessor implementation for local files and directories.
This is the lowest-priority accessor that handles any path-like source
that isn't handled by other accessors.
"""

from pathlib import Path
from typing import Union

from .base import DataAccessor, LocalResource, SourceType


class LocalAccessor(DataAccessor):
    """
    Local file system accessor.

    This accessor handles local files and directories. It should be
    registered with the lowest priority so that it only handles sources
    that aren't picked up by other accessors (Git, HTTP, Feishu, etc.).

    Features:
    - Handles any existing local path (file or directory)
    - Marks resources as non-temporary (since they're already local)
    - Provides clear local source type metadata
    """

    def can_handle(self, source: Union[str, Path]) -> bool:
        """
        Check if this accessor can handle the source.

        LocalAccessor accepts:
        1. Any Path object, OR
        2. Any string that looks like a local path (exists or not - we'll
           validate in access())

        Since this is a fallback accessor, it only returns True for sources
        that appear to be local paths (not URLs). The priority system ensures
        other accessors get a chance first.

        Args:
            source: Source string or Path object

        Returns:
            True if source appears to be a local path
        """
        from openviking.server.local_input_guard import is_remote_resource_source

        if isinstance(source, Path):
            return True

        source_str = str(source)

        # Don't handle remote URLs - those go to HTTPAccessor/GitAccessor
        if is_remote_resource_source(source_str):
            return False

        # For strings, accept anything that could be a local path
        # (we'll validate existence in access())
        return True

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Access a local file or directory.

        Simply wraps the local path in a LocalResource without any
        fetching or copying (since it's already local).

        Args:
            source: Local file path or Path object
            **kwargs: Additional arguments (unused for local accessor)

        Returns:
            LocalResource pointing to the local path

        Raises:
            FileNotFoundError: If the path does not exist
        """
        path = Path(source)

        # Validate that the path exists - preserve original behavior
        if not path.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")

        return LocalResource(
            path=path,
            source_type=SourceType.LOCAL,
            original_source=str(source),
            meta={
                "filename": path.name,
                "suffix": path.suffix.lower() if path.suffix else None,
                "is_dir": path.is_dir(),
            },
            is_temporary=False,
        )

    @property
    def priority(self) -> int:
        """
        Priority of this accessor.

        Returns 1 - the lowest priority, ensuring this accessor is only
        used when no other accessor can handle the source.

        Standard priority levels:
        - 100: Specific service (Feishu, etc.)
        - 80: Version control (Git, etc.)
        - 50: Generic protocols (HTTP, etc.)
        - 10: Fallback accessors
        - 1: Local file system (this one)

        Returns:
            Priority level 1
        """
        return 1
