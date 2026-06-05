"""Helper functions for common file operations in AGFS.

This module provides high-level helper functions for common operations:
- cp: Copy files/directories within AGFS
- upload: Upload files/directories from local filesystem to AGFS
- download: Download files/directories from AGFS to local filesystem
"""

from collections.abc import Iterator
from pathlib import Path

from .async_client import ensure_same_encryption_account
from .protocols import AGFSByteStream, AGFSSyncClientProtocol


def cp(
    client: AGFSSyncClientProtocol,
    src: str,
    dst: str,
    recursive: bool = False,
    stream: bool = False,
) -> None:
    """Copy a file or directory within AGFS.

    Args:
        client: AGFS binding client instance
        src: Source path in AGFS
        dst: Destination path in AGFS
        recursive: If True, copy directories recursively
        stream: If True, use streaming for large files (memory efficient)

    Raises:
        AGFSClientError: If source doesn't exist or operation fails

    Examples:
        >>> cp(client, "/file.txt", "/backup/file.txt")  # Copy file
        >>> cp(client, "/dir", "/backup/dir", recursive=True)  # Copy directory
    """
    ensure_same_encryption_account(src, dst)

    # Check if source exists and get its type
    src_info = client.stat(src)
    is_dir = src_info.get("isDir", False)

    if is_dir:
        if not recursive:
            raise ValueError(f"Cannot copy directory '{src}' without recursive=True")
        _copy_directory(client, src, dst, stream)
    else:
        _copy_file(client, src, dst, stream)


def upload(
    client: AGFSSyncClientProtocol,
    local_path: str,
    remote_path: str,
    recursive: bool = False,
    stream: bool = False,
) -> None:
    """Upload a file or directory from local filesystem to AGFS.

    Args:
        client: AGFS binding client instance
        local_path: Path to local file or directory
        remote_path: Destination path in AGFS
        recursive: If True, upload directories recursively
        stream: If True, use streaming for large files (memory efficient)

    Raises:
        FileNotFoundError: If local path doesn't exist
        AGFSClientError: If upload fails

    Examples:
        >>> upload(client, "/tmp/file.txt", "/remote/file.txt")  # Upload file
        >>> upload(client, "/tmp/data", "/remote/data", recursive=True)  # Upload directory
    """
    local = Path(local_path)

    if not local.exists():
        raise FileNotFoundError(f"Local path does not exist: {local_path}")

    if local.is_dir():
        if not recursive:
            raise ValueError(f"Cannot upload directory '{local_path}' without recursive=True")
        _upload_directory(client, local, remote_path, stream)
    else:
        _upload_file(client, local, remote_path, stream)


def download(
    client: AGFSSyncClientProtocol,
    remote_path: str,
    local_path: str,
    recursive: bool = False,
    stream: bool = False,
) -> None:
    """Download a file or directory from AGFS to local filesystem.

    Args:
        client: AGFS binding client instance
        remote_path: Path in AGFS
        local_path: Destination path on local filesystem
        recursive: If True, download directories recursively
        stream: If True, use streaming for large files (memory efficient)

    Raises:
        AGFSClientError: If remote path doesn't exist or download fails

    Examples:
        >>> download(client, "/remote/file.txt", "/tmp/file.txt")  # Download file
        >>> download(client, "/remote/data", "/tmp/data", recursive=True)  # Download directory
    """
    # Check if remote path exists and get its type
    remote_info = client.stat(remote_path)
    is_dir = remote_info.get("isDir", False)

    if is_dir:
        if not recursive:
            raise ValueError(f"Cannot download directory '{remote_path}' without recursive=True")
        _download_directory(client, remote_path, Path(local_path), stream)
    else:
        _download_file(client, remote_path, Path(local_path), stream)


# Internal helper functions


def _copy_file(client: AGFSSyncClientProtocol, src: str, dst: str, stream: bool) -> None:
    """Copy a single file within AGFS.

    Uses the raw byte channel (``read_raw``/``write_raw``) when available so a whole blob is moved
    verbatim, bypassing the encryption layer (ciphertext is copied as-is, never decrypted/re-encrypted).
    Falls back to ``cat``/``write`` for clients that do not expose the raw channel.
    """
    # Ensure parent directory exists
    _ensure_remote_parent_dir(client, dst)

    read_raw = getattr(client, "read_raw", None)
    write_raw = getattr(client, "write_raw", None)
    if callable(read_raw) and callable(write_raw):
        write_raw(dst, read_raw(src))
        return

    if stream:
        # The binding client returns bytes or an iterator of bytes, not an
        # HTTP response object with iter_content().
        client.write(dst, _iter_file_bytes(client.cat(src, stream=True)))
    else:
        # Read entire file and write
        data = client.cat(src)
        client.write(dst, data)


def _copy_directory(client: AGFSSyncClientProtocol, src: str, dst: str, stream: bool) -> None:
    """Recursively copy a directory within AGFS."""
    # Create destination directory
    try:
        client.mkdir(dst)
    except Exception:
        # Directory might already exist, continue
        pass

    # List source directory contents
    items = client.ls(src)

    for item in items:
        item_name = item["name"]
        src_path = f"{src.rstrip('/')}/{item_name}"
        dst_path = f"{dst.rstrip('/')}/{item_name}"

        if item.get("isDir", False):
            # Recursively copy subdirectory
            _copy_directory(client, src_path, dst_path, stream)
        else:
            # Copy file
            _copy_file(client, src_path, dst_path, stream)


def _upload_file(
    client: AGFSSyncClientProtocol,
    local_file: Path,
    remote_path: str,
    stream: bool,
) -> None:
    """Upload a single file to AGFS."""
    # Ensure parent directory exists in AGFS
    _ensure_remote_parent_dir(client, remote_path)

    if stream:
        client.write(remote_path, _iter_local_file_bytes(local_file))
    else:
        # Read entire file
        with open(local_file, "rb") as f:
            data = f.read()
        client.write(remote_path, data)


def _upload_directory(
    client: AGFSSyncClientProtocol,
    local_dir: Path,
    remote_path: str,
    stream: bool,
) -> None:
    """Recursively upload a directory to AGFS."""
    # Create remote directory
    try:
        client.mkdir(remote_path)
    except Exception:
        # Directory might already exist, continue
        pass

    # Walk through local directory
    for item in local_dir.iterdir():
        remote_item_path = f"{remote_path.rstrip('/')}/{item.name}"

        if item.is_dir():
            # Recursively upload subdirectory
            _upload_directory(client, item, remote_item_path, stream)
        else:
            # Upload file
            _upload_file(client, item, remote_item_path, stream)


def _download_file(
    client: AGFSSyncClientProtocol,
    remote_path: str,
    local_file: Path,
    stream: bool,
) -> None:
    """Download a single file from AGFS."""
    # Ensure parent directory exists locally
    local_file.parent.mkdir(parents=True, exist_ok=True)

    if stream:
        with open(local_file, "wb") as f:
            for chunk in _iter_file_bytes(client.cat(remote_path, stream=True)):
                f.write(chunk)
    else:
        # Read entire file
        data = client.cat(remote_path)
        with open(local_file, "wb") as f:
            f.write(data)


def _download_directory(
    client: AGFSSyncClientProtocol,
    remote_path: str,
    local_dir: Path,
    stream: bool,
) -> None:
    """Recursively download a directory from AGFS."""
    # Create local directory
    local_dir.mkdir(parents=True, exist_ok=True)

    # List remote directory contents
    items = client.ls(remote_path)

    for item in items:
        item_name = item["name"]
        remote_item_path = f"{remote_path.rstrip('/')}/{item_name}"
        local_item_path = local_dir / item_name

        if item.get("isDir", False):
            # Recursively download subdirectory
            _download_directory(client, remote_item_path, local_item_path, stream)
        else:
            # Download file
            _download_file(client, remote_item_path, local_item_path, stream)


def _ensure_remote_parent_dir(client: AGFSSyncClientProtocol, path: str) -> None:
    """Ensure the parent directory exists for a remote path."""
    parent = "/".join(path.rstrip("/").split("/")[:-1])
    if parent and parent != "/":
        # Try to create parent directory (and its parents)
        _ensure_remote_dir_recursive(client, parent)


def _ensure_remote_dir_recursive(client: AGFSSyncClientProtocol, path: str) -> None:
    """Recursively ensure a directory exists in AGFS."""
    if not path or path == "/":
        return

    # Check if directory already exists
    try:
        info = client.stat(path)
        if info.get("isDir", False):
            return  # Directory exists
    except Exception:
        # Directory doesn't exist, need to create it
        pass

    # Ensure parent exists first
    parent = "/".join(path.rstrip("/").split("/")[:-1])
    if parent and parent != "/":
        _ensure_remote_dir_recursive(client, parent)

    # Create this directory
    try:
        client.mkdir(path)
    except Exception:
        # Might already exist due to race condition, ignore
        pass


def _iter_file_bytes(data: bytes | AGFSByteStream) -> AGFSByteStream:
    """Normalize AGFS read results to a byte iterator."""
    if isinstance(data, bytes):
        return iter((data,))
    if isinstance(data, Iterator):
        return data
    return iter(data)


def _iter_local_file_bytes(local_file: Path, chunk_size: int = 8192) -> AGFSByteStream:
    """Yield local file content in chunks for streaming uploads."""
    with open(local_file, "rb") as file_obj:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk
