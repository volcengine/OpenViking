# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
File system implementations for transaction module.

Provides file system interfaces for AGFS and local file systems.
"""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Union


class FileSystemBase(ABC):
    """File system abstract base class."""

    @abstractmethod
    async def stat(self, path: str) -> Dict[str, Any]:
        """Get file/directory metadata.

        Args:
            path: Path to stat

        Returns:
            Dictionary containing file metadata (e.g., isDir, size, etc.)
        """
        pass

    @abstractmethod
    async def read(self, path: str, offset: int = 0, size: int = -1) -> bytes:
        """Read file content.

        Args:
            path: Path to read
            offset: Byte offset to start reading from
            size: Number of bytes to read (-1 for all)

        Returns:
            File content as bytes
        """
        pass

    @abstractmethod
    async def write(self, path: str, data: Union[bytes, str]) -> str:
        """Write data to file.

        Args:
            path: Path to write to
            data: Data to write (bytes or string)

        Returns:
            Path of written file
        """
        pass

    @abstractmethod
    async def mkdir(self, path: str, mode: str = "755", exist_ok: bool = False) -> None:
        """Create directory.

        Args:
            path: Directory path to create
            mode: Directory permissions (octal string)
            exist_ok: If True, don't raise error if directory exists
        """
        pass

    @abstractmethod
    async def rm(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        """Remove file or directory.

        Args:
            path: Path to remove
            recursive: If True, remove directory and all contents

        Returns:
            Result dictionary
        """
        pass

    @abstractmethod
    async def ls(self, path: str) -> List[Dict[str, Any]]:
        """List directory contents.

        Args:
            path: Directory path to list

        Returns:
            List of entry dictionaries (e.g., name, isDir, size, etc.)
        """
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if path exists.

        Args:
            path: Path to check

        Returns:
            True if path exists, False otherwise
        """
        pass


class AGFSFileSystem(FileSystemBase):
    """AGFS file system implementation."""

    def __init__(self, agfs_client: Any):
        """Initialize AGFSFileSystem.

        Args:
            agfs_client: AGFSClient instance
        """
        self.agfs = agfs_client

    async def stat(self, path: str) -> Dict[str, Any]:
        """Get file/directory metadata."""
        return await self.agfs.stat(path)

    async def read(self, path: str, offset: int = 0, size: int = -1) -> bytes:
        """Read file content."""
        return await self.agfs.read(path, offset, size)

    async def write(self, path: str, data: Union[bytes, str]) -> str:
        """Write data to file."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return await self.agfs.write(path, data)

    async def mkdir(self, path: str, mode: str = "755", exist_ok: bool = False) -> None:
        """Create directory."""
        if exist_ok:
            try:
                await self.stat(path)
                return
            except Exception:
                pass
        await self.agfs.mkdir(path)

    async def rm(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        """Remove file or directory."""
        return await self.agfs.rm(path, recursive)

    async def ls(self, path: str) -> List[Dict[str, Any]]:
        """List directory contents."""
        return await self.agfs.ls(path)

    async def exists(self, path: str) -> bool:
        """Check if path exists."""
        try:
            await self.stat(path)
            return True
        except Exception:
            return False


class LocalFileSystem(FileSystemBase):
    """Local file system implementation for testing."""

    def __init__(self, root_dir: str = "/tmp/openviking_test"):
        """Initialize LocalFileSystem.

        Args:
            root_dir: Root directory for file operations
        """
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to root directory."""
        resolved = self.root_dir / path.lstrip("/")
        return resolved

    async def stat(self, path: str) -> Dict[str, Any]:
        """Get file/directory metadata."""
        resolved = self._resolve_path(path).resolve()
        stat_info = resolved.stat()
        return {
            "isDir": resolved.is_dir(),
            "size": stat_info.st_size,
            "mode": oct(stat_info.st_mode)[-3:],
            "mtime": stat_info.st_mtime,
        }

    async def read(self, path: str, offset: int = 0, size: int = -1) -> bytes:
        """Read file content."""
        resolved = self._resolve_path(path)
        with open(resolved, "rb") as f:
            f.seek(offset)
            if size == -1:
                return f.read()
            return f.read(size)

    async def write(self, path: str, data: Union[bytes, str]) -> str:
        """Write data to file."""
        resolved = self._resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        with open(resolved, "wb") as f:
            f.write(data)
        return str(resolved)

    async def mkdir(self, path: str, mode: str = "755", exist_ok: bool = False) -> None:
        """Create directory."""
        resolved = self._resolve_path(path)
        if exist_ok and resolved.exists():
            return
        resolved.mkdir(parents=True, exist_ok=exist_ok)

    async def rm(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        """Remove file or directory."""
        resolved = self._resolve_path(path)
        if recursive:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        else:
            resolved.unlink()
        return {"path": str(resolved), "recursive": recursive}

    async def ls(self, path: str) -> List[Dict[str, Any]]:
        """List directory contents."""
        resolved = self._resolve_path(path)
        entries = []
        for item in resolved.iterdir():
            stat_info = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "isDir": item.is_dir(),
                    "size": stat_info.st_size,
                }
            )
        return entries

    async def exists(self, path: str) -> bool:
        """Check if path exists."""
        resolved = self._resolve_path(path)
        return resolved.exists()
