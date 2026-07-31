"""Protocol types for synchronous AGFS-like clients."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, BinaryIO, Dict, Literal, Protocol, overload, runtime_checkable

AGFSByteStream = Iterator[bytes]


@runtime_checkable
class AGFSSyncClientProtocol(Protocol):
    """Minimal synchronous AGFS client contract used by OpenViking."""

    def ls(self, path: str = "/") -> list[Dict[str, Any]]:
        """List directory entries under the given AGFS path."""

    @overload
    def read(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[False] = False,
    ) -> bytes:
        """Read file content from AGFS."""

    @overload
    def read(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[True] = True,
    ) -> AGFSByteStream:
        """Read file content from AGFS."""

    @overload
    def cat(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[False] = False,
    ) -> bytes:
        """Read file content from AGFS."""

    @overload
    def cat(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: Literal[True] = True,
    ) -> AGFSByteStream:
        """Read file content or a byte stream from AGFS."""

    def write(
        self,
        path: str,
        data: bytes | Iterator[bytes] | BinaryIO,
        max_retries: int = 3,
    ) -> str:
        """Write file content to AGFS and return the backend result."""

    def mkdir(self, path: str, mode: str = "755") -> Dict[str, Any]:
        """Create a directory in AGFS."""

    def ensure_parent_dirs(self, path: str, mode: str = "755") -> Dict[str, Any]:
        """Ensure parent directories exist for the given AGFS path."""

    def rm(self, path: str, recursive: bool = False, force: bool = True) -> Dict[str, Any]:
        """Remove a file or directory from AGFS."""

    def stat(self, path: str) -> Dict[str, Any]:
        """Return AGFS metadata for the given path."""

    def mv(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Move or rename a path inside AGFS."""

    def copy_within_mount(self, src_path: str, dst_path: str) -> Dict[str, Any]:
        """Attempt a same-mount verbatim copy and report whether it was used."""

    def grep(self, **kwargs: Any) -> Dict[str, Any]:
        """Run a grep-like search through the AGFS backend."""

    def tree_directory(
        self,
        path: str,
        show_hidden: bool = False,
        node_limit: int | None = None,
        level_limit: int | None = None,
    ) -> list[Dict[str, Any]]:
        """Return a tree view for the given AGFS directory."""

    def glob_directory(
        self,
        path: str,
        pattern: str,
        show_hidden: bool = False,
        page_size: int | None = None,
        level_limit: int | None = None,
        continuation_token: str | None = None,
    ) -> Dict[str, Any]:
        """Return one page of flat glob matches for the given AGFS directory."""

    def system_sync_status(self, path: str) -> Dict[str, Any]:
        """Return multi-write sync status for a file or directory path."""

    def system_sync_retry(self, path: str) -> Dict[str, Any]:
        """Retry pending multi-write sync work for a file or directory path."""
