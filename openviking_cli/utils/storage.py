# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Storage path management for OpenViking.

Manages file storage in .openviking/ directory for media files (images, tables, etc.).
"""

import shutil
from pathlib import Path
from typing import Optional
from uuid import uuid4

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class StoragePath:
    """
    Manages .openviking/ directory for storing extracted media files.

    Directory structure:
    .openviking/
    ├── media/
    │   ├── <resource_name>/
    │   │   ├── images/
    │   │   │   ├── 001.png
    │   │   │   └── 002.png
    │   │   └── tables/
    │   │       ├── 001.png
    │   │       └── 002.png
    │   └── ...
    └── downloads/
        └── <temp_files>
    """

    BASE_DIR = ".openviking"
    MEDIA_DIR = "media"
    DOWNLOADS_DIR = "downloads"

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize storage path manager.

        Args:
            base_path: Base directory for .openviking folder.
                      If None, uses current working directory.
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self.openviking_dir = self.base_path / self.BASE_DIR
        self.media_dir = self.openviking_dir / self.MEDIA_DIR
        self.downloads_dir = self.openviking_dir / self.DOWNLOADS_DIR

    def ensure_dirs(self) -> None:
        """Create necessary directories if they don't exist."""
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def get_resource_media_dir(self, resource_name: str, media_type: str = "images") -> Path:
        """
        Get directory for storing resource media files.

        Args:
            resource_name: Name of the resource (e.g., "Industry Analysis")
            media_type: Type of media ("images" or "tables")

        Returns:
            Path to the media directory
        """
        # Sanitize resource name for filesystem
        safe_name = self._sanitize_name(resource_name)
        media_path = self.media_dir / safe_name / media_type
        media_path.mkdir(parents=True, exist_ok=True)
        return media_path

    def save_image(
        self,
        resource_name: str,
        image_data: bytes,
        filename: Optional[str] = None,
        extension: str = ".png",
    ) -> Path:
        """
        Save an image file.

        Args:
            resource_name: Name of the resource
            image_data: Image bytes
            filename: Optional filename (without extension)
            extension: File extension (default: .png)

        Returns:
            Path to saved image
        """
        images_dir = self.get_resource_media_dir(resource_name, "images")

        if filename is None:
            # Generate sequential filename
            existing = list(images_dir.glob(f"*{extension}"))
            filename = f"{len(existing) + 1:03d}"

        file_path = images_dir / f"{filename}{extension}"
        file_path.write_bytes(image_data)
        logger.debug(f"Saved image: {file_path}")
        return file_path

    def save_table_image(
        self,
        resource_name: str,
        image_data: bytes,
        filename: Optional[str] = None,
        extension: str = ".png",
    ) -> Path:
        """
        Save a table image file.

        Args:
            resource_name: Name of the resource
            image_data: Image bytes
            filename: Optional filename (without extension)
            extension: File extension (default: .png)

        Returns:
            Path to saved table image
        """
        tables_dir = self.get_resource_media_dir(resource_name, "tables")

        if filename is None:
            existing = list(tables_dir.glob(f"*{extension}"))
            filename = f"{len(existing) + 1:03d}"

        file_path = tables_dir / f"{filename}{extension}"
        file_path.write_bytes(image_data)
        logger.debug(f"Saved table image: {file_path}")
        return file_path

    def get_download_path(self, filename: Optional[str] = None, extension: str = ".pdf") -> Path:
        """
        Get path for downloading a file.

        Args:
            filename: Optional filename
            extension: File extension

        Returns:
            Path for the download
        """
        self.ensure_dirs()

        if filename is None:
            filename = str(uuid4())

        return self.downloads_dir / f"{filename}{extension}"

    def cleanup_resource_media(self, resource_name: str) -> None:
        """
        Remove all media files for a resource.

        Args:
            resource_name: Name of the resource
        """
        safe_name = self._sanitize_name(resource_name)
        resource_dir = self.media_dir / safe_name

        if resource_dir.exists():
            shutil.rmtree(resource_dir)
            logger.info(f"Cleaned up media for resource: {resource_name}")

    def cleanup_downloads(self) -> None:
        """Remove all downloaded files."""
        if self.downloads_dir.exists():
            shutil.rmtree(self.downloads_dir)
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Cleaned up downloads directory")

    def cleanup_all(self) -> None:
        """Remove all OpenViking storage."""
        if self.openviking_dir.exists():
            shutil.rmtree(self.openviking_dir)
            logger.info("Cleaned up all OpenViking storage")

    def get_all_resource_media(self, resource_name: str) -> dict:
        """
        Get all media files for a resource.

        Args:
            resource_name: Name of the resource

        Returns:
            Dictionary with "images" and "tables" lists of file paths
        """
        safe_name = self._sanitize_name(resource_name)
        resource_dir = self.media_dir / safe_name

        result = {"images": [], "tables": []}

        if resource_dir.exists():
            images_dir = resource_dir / "images"
            tables_dir = resource_dir / "tables"

            if images_dir.exists():
                result["images"] = sorted(images_dir.glob("*"))

            if tables_dir.exists():
                result["tables"] = sorted(tables_dir.glob("*"))

        return result

    def get_storage_stats(self) -> dict:
        """
        Get storage statistics.

        Returns:
            Dictionary with storage statistics
        """
        stats = {
            "total_size": 0,
            "resources": {},
            "downloads_size": 0,
        }

        if not self.openviking_dir.exists():
            return stats

        # Calculate media sizes
        if self.media_dir.exists():
            for resource_dir in self.media_dir.iterdir():
                if resource_dir.is_dir():
                    size = sum(f.stat().st_size for f in resource_dir.rglob("*") if f.is_file())
                    stats["resources"][resource_dir.name] = size
                    stats["total_size"] += size

        # Calculate downloads size
        if self.downloads_dir.exists():
            stats["downloads_size"] = sum(
                f.stat().st_size for f in self.downloads_dir.rglob("*") if f.is_file()
            )
            stats["total_size"] += stats["downloads_size"]

        return stats

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """
        Sanitize a name for use in filesystem paths.

        Args:
            name: Original name

        Returns:
            Sanitized name safe for filesystem
        """
        import re

        # Remove or replace unsafe characters
        safe = re.sub(r'[<>:"/\\|?*]', "_", name)
        # Replace multiple underscores with single
        safe = re.sub(r"_+", "_", safe)
        # Remove leading/trailing underscores and spaces
        safe = safe.strip("_ ")
        # Limit length
        return safe[:100] if safe else "unnamed"


# Default storage instance
_default_storage: Optional[StoragePath] = None


def get_storage(base_path: Optional[Path] = None) -> StoragePath:
    """
    Get storage path manager.

    Args:
        base_path: Optional base path for storage

    Returns:
        StoragePath instance
    """
    global _default_storage

    if base_path is not None:
        return StoragePath(base_path)

    if _default_storage is None:
        _default_storage = StoragePath()

    return _default_storage
